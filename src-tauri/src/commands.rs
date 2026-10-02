//! Tauri command layer: the bridge between the UI and `x-core`.
//!
//! Every command is a thin wrapper. All business logic lives in
//! `x-core` so it stays testable without a window.
//!
//! Two rules hold throughout:
//!   * Nothing here logs or returns a credential. Secrets are read in
//!     `x_core::config` and never leave the backend.
//!   * Every paid action is preceded by a local validation step, so the
//!     UI can show what a click will cost before it spends anything.

use std::path::PathBuf;
use std::sync::Arc;

use parking_lot::Mutex;
use serde::{Deserialize, Serialize};
use tauri::{Manager, State};
use tauri_plugin_opener::OpenerExt;
use x_core::ai::client::AiClient;
use x_core::ai::prompts::ProjectRef;
use x_core::ai::projects as projects_csv;
use x_core::ai::workflow::DraftWorkflow;
use x_core::ai::agent::AgentDraftWorkflow;
use x_core::config::{self, Settings};
use x_core::error::Error as CoreError;
use x_core::store::models::{Draft, Project};
use x_core::store::repos::{Database, IncomingTweet};
use x_core::utils::media_catalog as catalog;
use x_core::utils::media_library;
use x_core::utils::media_matching::MediaCandidate;
use x_core::utils::virality;
use x_core::x::auth::TokenManager;
use x_core::x::client::XClient;
use x_core::x::publish;

/// Long-lived app state. One niche is active at a time, matching the
/// Streamlit app's niche switcher.
///
/// Both handles store `Arc`s so a command can clone what it needs and
/// drop the guard *before* any `.await`. Holding a `MutexGuard` across an
/// await is what forces the future to be `Send`, and `Database`'s SQLite
/// connection is deliberately not thread-safe.
pub struct AppState {
    db: Mutex<Option<Arc<Database>>>,
    inner: Mutex<Option<Arc<Runtime>>>,
    pending_auth: Mutex<Option<PendingAuth>>,
}

struct PendingAuth {
    listener: std::net::TcpListener,
    verifier: String,
    csrf: String,
    redirect_uri: String,
    runtime: Arc<Runtime>,
}

struct Runtime {
    settings: Settings,
    tokens: Arc<TokenManager>,
    x: Arc<XClient>,
    ai: Arc<AiClient>,
}

impl AppState {
    fn new() -> Self {
        Self { db: Mutex::new(None), inner: Mutex::new(None), pending_auth: Mutex::new(None) }
    }
}

/// Serialize an error for the frontend, keeping the message but never
/// leaking a token (the core never puts one in a message).
#[derive(Debug, Serialize)]
pub struct CmdError {
    pub kind: String,
    pub message: String,
}

impl From<CoreError> for CmdError {
    fn from(e: CoreError) -> Self {
        CmdError { kind: e.kind().into(), message: e.to_string() }
    }
}

pub type CmdResult<T> = Result<T, CmdError>;

/// Ensure the DB and clients exist for `niche`, rebuilding them if the
/// niche changed, then return owned handles.
///
/// The returned `Arc`s let the caller drop every guard before awaiting.
fn ensure_runtime(
    state: &State<'_, AppState>,
    niche: &str,
) -> CmdResult<(Arc<Database>, Arc<Runtime>)> {
    {
        let current = state.inner.lock();
        if let Some(rt) = current.as_ref() {
            if rt.settings.niche == niche {
                let db = state.db.lock().clone();
                if let Some(db) = db {
                    return Ok((db, rt.clone()));
                }
            }
        }
    }

    let settings = config::get_settings(niche);
    std::fs::create_dir_all(&settings.data_dir).map_err(|e| CmdError {
        kind: "io".into(),
        message: format!("create {}: {e}", settings.data_dir.display()),
    })?;

    let db_path = settings.data_dir.join("state.db");
    let db = Arc::new(Database::open(&db_path)?);
    projects_csv::sync_projects(&settings, &db)?;
    tracing::info!(niche = %niche, db = %db_path.display(), "runtime ready");

    // One token manager shared by the X client, so a refresh triggered by
    // any caller is visible to all of them.
    let tokens = Arc::new(TokenManager::new(settings.clone())?);
    let runtime = Arc::new(Runtime {
        settings,
        x: Arc::new(XClient::new(config::get_settings(niche), tokens.clone())?),
        ai: Arc::new(AiClient::new(config::get_settings(niche))?),
        tokens,
    });

    *state.db.lock() = Some(db.clone());
    *state.inner.lock() = Some(runtime.clone());
    Ok((db, runtime))
}

fn with_db<T>(
    state: &State<'_, AppState>,
    f: impl FnOnce(&Database) -> CmdResult<T>,
) -> CmdResult<T> {
    let guard = state.db.lock();
    let db = guard.as_ref().ok_or_else(|| CmdError {
        kind: "internal".into(),
        message: "database not initialized; call bootstrap first".into(),
    })?;
    f(db)
}

// ---- bootstrap / status ----------------------------------------------------

#[derive(Debug, Serialize)]
pub struct AppInfo {
    pub niche: String,
    pub data_dir: String,
    pub config_dir: String,
    pub repo_root: String,
    pub page_title: String,
    pub model_id: String,
    pub ai_configured: bool,
    pub x_configured: bool,
    pub has_oauth_tokens: bool,
    pub creators: Vec<String>,
    pub projects: Vec<Project>,
    pub niches: Vec<String>,
    pub cost_warning_threshold_usd: f64,
}

#[tauri::command]
pub fn bootstrap(state: State<'_, AppState>, niche: String) -> CmdResult<AppInfo> {
    let (_db, rt) = ensure_runtime(&state, &niche)?;
    let info = AppInfo {
        niche: rt.settings.niche.clone(),
        data_dir: rt.settings.data_dir.to_string_lossy().to_string(),
        config_dir: rt.settings.config_dir.to_string_lossy().to_string(),
        repo_root: rt.settings.repo_root.to_string_lossy().to_string(),
        page_title: rt.settings.ui.page_title.clone(),
        model_id: rt.settings.minimax.model_id.clone(),
        ai_configured: rt.ai.configured(),
        x_configured: rt.settings.x.configured(),
        has_oauth_tokens: rt.tokens.store_path().exists(),
        creators: rt.settings.accounts.iter().map(|a| a.handle.clone()).collect(),
        projects: with_db(&state, |db| Ok(db.list_projects()?))?,
        niches: config::SUPPORTED_NICHES.iter().map(|s| s.to_string()).collect(),
        cost_warning_threshold_usd: rt.settings.ui.cost_warning_threshold_usd,
    };
    Ok(info)
}

// ---- creators --------------------------------------------------------------

#[derive(Debug, Serialize)]
pub struct CreatorsResult {
    pub handles: Vec<String>,
    pub saved_to: String,
}

#[tauri::command]
pub fn save_creators(
    state: State<'_, AppState>,
    niche: String,
    handles: Vec<String>,
) -> CmdResult<CreatorsResult> {
    let (_db, rt) = ensure_runtime(&state, &niche)?;
    let rows = config::write_accounts(&rt.settings.config_dir, &handles);
    config::clear_settings_cache();
    Ok(CreatorsResult {
        handles: rows.into_iter().map(|a| a.handle).collect(),
        saved_to: rt.settings.config_dir.join("creators.yaml").to_string_lossy().to_string(),
    })
}

// ---- projects (activations) ------------------------------------------------

#[tauri::command]
pub fn save_projects(
    state: State<'_, AppState>,
    niche: String,
    projects: Vec<Project>,
) -> CmdResult<usize> {
    let (_db, rt) = ensure_runtime(&state, &niche)?;
    let path = projects_csv::csv_path(&rt.settings);
    projects_csv::write_csv(&path, &projects)?;
    let count = with_db(&state, |db| {
        db.replace_projects(&projects)?;
        Ok(db.list_projects()?.len())
    })?;
    // Media folders follow the project list, so create/drop as needed.
    let names: Vec<String> = projects.iter().map(|p| p.name.clone()).collect();
    let cache = rt.settings.data_dir.join("media_cache");
    let _ = media_library::ensure_project_media_dirs(&cache, &names);
    Ok(count)
}

#[tauri::command]
pub fn list_projects(state: State<'_, AppState>) -> CmdResult<Vec<Project>> {
    with_db(&state, |db| Ok(db.list_projects()?))
}

// ---- sources: tweets -------------------------------------------------------

#[derive(Debug, Serialize)]
pub struct TweetRow {
    #[serde(flatten)]
    pub tweet: x_core::store::models::Tweet,
    pub velocity: f64,
    pub hot: bool,
}

#[derive(Debug, Serialize)]
pub struct FetchResult {
    pub handle: String,
    pub new_tweets: usize,
    pub fetched: usize,
    pub estimated_cost_usd: f64,
    pub display_name: String,
}

#[tauri::command]
pub async fn fetch_recent(
    state: State<'_, AppState>,
    niche: String,
    handles: Vec<String>,
) -> CmdResult<Vec<FetchResult>> {
    let (_db, rt) = ensure_runtime(&state, &niche)?;

    let mut out = Vec::new();
    for handle in handles {
        let handle = handle.trim_start_matches('@').trim().to_string();
        if handle.is_empty() {
            continue;
        }
        // 1. Resolve the handle to a user id ($0.010).
        let user = rt.x.get_user_by_username(&handle).await?;

        // 2. Pull recent posts ($0.005 each).
        let tweets = rt
            .x
            .get_user_tweets(
                &user.id,
                rt.settings.x.recent_max_results as usize,
                &rt.settings.x.exclude,
            )
            .await?;

        let incoming: Vec<IncomingTweet> = tweets
            .into_iter()
            .map(|t| IncomingTweet {
                id: t.id,
                text: t.text,
                created_at: t.created_at,
                public_metrics: t.public_metrics,
                quote_tweet_id: None,
                quote_tweet_text: None,
                quote_tweet_author_id: None,
                source_image_url: t.source_image_url,
            })
            .collect();
        let count = incoming.len();

        let new_count = with_db(&state, |db| {
            db.upsert_account(&handle, &user.id, &user.name)?;
            db.mark_account_fetched(&handle, None)?;
            Ok(db.upsert_tweets(&handle, &incoming)?)
        })?;

        let cost = x_core::x::costs::estimate_read_cost(count as i64, 1);
        out.push(FetchResult {
            handle,
            new_tweets: new_count,
            fetched: count,
            estimated_cost_usd: cost,
            display_name: user.name,
        });
    }
    Ok(out)
}

#[derive(Debug, Deserialize)]
pub struct TweetQuery {
    pub status: Option<String>,
    pub limit: Option<usize>,
}

#[tauri::command]
pub fn list_tweets(state: State<'_, AppState>, query: Option<TweetQuery>) -> CmdResult<Vec<TweetRow>> {
    let q = query.unwrap_or(TweetQuery { status: None, limit: Some(200) });
    let rows = with_db(&state, |db| Ok(db.list_tweets(q.status.as_deref(), q.limit.unwrap_or(200))?))?;

    // Velocity scoring needs a single reference time for the whole page.
    let reference = chrono::Utc::now();
    let scored: Vec<virality::ScoredTweet> = rows
        .iter()
        .map(|t| virality::ScoredTweet {
            id: t.id.clone(),
            public_metrics: virality::Metrics::from_map(&t.public_metrics),
            created_at: Some(t.created_at.to_rfc3339()),
        })
        .collect();
    let hot = virality::get_hot_opportunity_ids(&scored, 5, 2.0, Some(reference));

    Ok(rows
        .into_iter()
        .map(|tweet| {
            let created = tweet.created_at.to_rfc3339();
            let velocity = virality::compute_engagement_velocity(
                &virality::Metrics::from_map(&tweet.public_metrics),
                Some(&created),
                Some(reference),
            );
            TweetRow { hot: hot.contains(&tweet.id), velocity, tweet }
        })
        .collect())
}

#[tauri::command]
pub fn set_tweet_statuses(
    state: State<'_, AppState>,
    ids: Vec<String>,
    status: String,
) -> CmdResult<usize> {
    with_db(&state, |db| Ok(db.set_tweet_statuses(&ids, &status)?))
}

// ---- create: draft generation ---------------------------------------------

#[derive(Debug, Deserialize)]
pub struct GenerateRequest {
    pub niche: String,
    pub source_text: String,
    pub source_author: String,
    pub source_tweet_id: Option<String>,
    pub writing_mode: Option<String>,
    pub extra_instructions: Option<String>,
    pub image_paths: Option<Vec<String>>,
    pub two_options: Option<bool>,
}

#[derive(Debug, Serialize)]
pub struct GenerateResponse {
    pub workflow: Option<serde_json::Value>,
    pub option_a: Option<String>,
    pub option_a_reasoning: Option<String>,
    pub option_b: Option<String>,
    pub option_b_reasoning: Option<String>,
    pub topic: String,
    pub project_name: String,
    pub project_url: String,
    pub cta_text: String,
    pub recommended_media_path: Option<String>,
    pub recommended_media_name: Option<String>,
}

#[tauri::command]
pub async fn generate_draft(
    state: State<'_, AppState>,
    req: GenerateRequest,
) -> CmdResult<GenerateResponse> {
    let (_db, rt) = ensure_runtime(&state, &req.niche)?;
    let list = with_db(&state, |db| Ok(db.list_projects()?))?;
    let projects: Vec<ProjectRef> = list
        .into_iter()
        .map(|p| ProjectRef::new(p.name, p.url))
        .collect();

    if req.two_options.unwrap_or(false) {
        // Agent path: Option A + Option B + a media recommendation.
        let wf = AgentDraftWorkflow::new(&rt.ai, &req.niche, Some(&rt.settings.data_dir));
        let r = wf
            .run(
                &req.source_text,
                &req.source_author,
                req.source_tweet_id.clone(),
                projects,
                req.extra_instructions.as_deref().unwrap_or(""),
            )
            .await?;
        Ok(GenerateResponse {
            workflow: None,
            option_a: Some(r.option_a_main.clone()),
            option_a_reasoning: Some(r.option_a_reasoning.clone()),
            option_b: Some(r.option_b_main.clone()),
            option_b_reasoning: Some(r.option_b_reasoning.clone()),
            topic: r.topic,
            project_name: r.project_name,
            project_url: r.project_url,
            cta_text: r.cta_text,
            recommended_media_path: r.recommended_media_path,
            recommended_media_name: r.recommended_media_name,
        })
    } else {
        // Single-draft path: one rephrase call + one match/CTA call.
        let wf = DraftWorkflow::new(&rt.ai);
        let r = wf
            .run(
                &req.source_text,
                &req.source_author,
                req.source_tweet_id.clone(),
                projects,
                req.writing_mode.as_deref().unwrap_or("rephrase"),
                req.extra_instructions.as_deref().unwrap_or(""),
                req.image_paths.clone().unwrap_or_default(),
                &req.niche,
            )
            .await?;
        Ok(GenerateResponse {
            workflow: Some(serde_json::to_value(&r.draft).unwrap_or_default()),
            option_a: Some(r.draft.body.clone()),
            option_a_reasoning: Some(r.rephrase_reasoning.clone()),
            option_b: None,
            option_b_reasoning: None,
            topic: r.topic,
            project_name: r.project_name,
            project_url: r.project_url,
            cta_text: r.cta_text,
            recommended_media_path: None,
            recommended_media_name: None,
        })
    }
}

#[tauri::command]
pub fn save_draft(state: State<'_, AppState>, draft: Draft) -> CmdResult<i64> {
    with_db(&state, |db| Ok(db.create_draft(&draft)?))
}

// ---- queue: drafts, publish, history ---------------------------------------

#[derive(Debug, Deserialize)]
pub struct DraftQuery {
    pub status: Option<String>,
    pub limit: Option<usize>,
}

#[tauri::command]
pub fn list_drafts(state: State<'_, AppState>, query: Option<DraftQuery>) -> CmdResult<Vec<Draft>> {
    let q = query.unwrap_or(DraftQuery { status: None, limit: Some(50) });
    with_db(&state, |db| Ok(db.list_drafts(q.status.as_deref(), q.limit.unwrap_or(50))?))
}

#[tauri::command]
pub fn update_draft(state: State<'_, AppState>, draft: Draft) -> CmdResult<()> {
    with_db(&state, |db| {
        db.update_draft(&draft)?;
        Ok(())
    })
}

#[tauri::command]
pub fn delete_draft(state: State<'_, AppState>, draft_id: i64) -> CmdResult<()> {
    with_db(&state, |db| Ok(db.delete_draft(draft_id)?))
}

#[derive(Debug, Serialize)]
pub struct CostPreview {
    pub main: f64,
    pub reply: f64,
    pub total: f64,
    pub reason: String,
    pub saved: f64,
    pub inline_alternative: Option<f64>,
    pub errors: Vec<x_core::utils::text::PostValidationError>,
    pub can_post: bool,
}

/// Cost + validity preview for a draft, with no side effects. The UI
/// calls this before every "Post now".
#[tauri::command]
pub fn preview_publish(draft: Draft) -> CostPreview {
    let errors = publish::validate_draft(&draft);
    let cost = publish::preview_publish_cost(&draft);
    CostPreview {
        main: cost.main,
        reply: cost.reply,
        total: cost.total,
        reason: cost.reason,
        saved: cost.saved,
        inline_alternative: cost.inline_alternative,
        can_post: errors.is_empty(),
        errors,
    }
}

#[tauri::command]
pub async fn post_draft(
    state: State<'_, AppState>,
    niche: String,
    draft_id: i64,
) -> CmdResult<publish::PublishResult> {
    let (db, rt) = ensure_runtime(&state, &niche)?;

    let mut draft = with_db(&state, |db| Ok(db.get_draft(draft_id)?))?.ok_or_else(|| CmdError {
        kind: "not_found".into(),
        message: format!("draft {draft_id} not found"),
    })?;

    // `db` is an owned Arc and `rt` an owned Arc: no lock guard is held
    // while this awaits, and `&Database` is `Send` because `Database` is
    // internally synchronized.
    match publish::publish_draft(&rt.settings, &db, &rt.x, &mut draft).await {
        Ok(res) => Ok(res),
        Err(e) => {
            let failed_id = draft.id;
            let detail = e.to_string();
            let _ = with_db(&state, |db| {
                db.log_post(failed_id, "post_now", None, "failed", &detail)?;
                Ok(())
            });
            Err(e.into())
        }
    }
}

#[derive(Debug, Serialize)]
pub struct HistoryRow {
    pub id: Option<i64>,
    pub draft_id: Option<i64>,
    pub action: String,
    pub cost_usd: Option<f64>,
    pub result: Option<String>,
    pub detail: String,
    pub created_at: Option<String>,
}

#[tauri::command]
pub fn recent_history(state: State<'_, AppState>, limit: Option<usize>) -> CmdResult<Vec<HistoryRow>> {
    let entries = with_db(&state, |db| Ok(db.recent_log(limit.unwrap_or(20))?))?;
    Ok(entries
        .into_iter()
        .map(|e| HistoryRow {
            id: e.id,
            draft_id: e.draft_id,
            action: e.action,
            cost_usd: e.cost_usd,
            result: e.result,
            detail: e.detail,
            created_at: e.created_at.map(|d| d.to_rfc3339()),
        })
        .collect())
}

#[derive(Debug, Serialize)]
pub struct MeterSummaryDto {
    pub posts_read: i64,
    pub profiles_read: i64,
    pub reads_cost_usd: f64,
    pub writes_cost_usd: f64,
    pub total_cost_usd: f64,
    pub logged_cost_usd: f64,
}

#[tauri::command]
pub fn session_meter(state: State<'_, AppState>, niche: String) -> CmdResult<MeterSummaryDto> {
    let (_db, rt) = ensure_runtime(&state, &niche)?;
    let s = rt.x.meter_snapshot().summary();
    let logged = with_db(&state, |db| Ok(db.total_session_cost()?))?;
    Ok(MeterSummaryDto {
        posts_read: s.posts_read,
        profiles_read: s.profiles_read,
        reads_cost_usd: s.reads_cost_usd,
        writes_cost_usd: s.writes_cost_usd,
        total_cost_usd: s.total_cost_usd,
        logged_cost_usd: logged,
    })
}

// ---- media library ---------------------------------------------------------

#[tauri::command]
pub fn list_project_folders(state: State<'_, AppState>, niche: String) -> CmdResult<Vec<String>> {
    let (_db, rt) = ensure_runtime(&state, &niche)?;
    let cache = rt.settings.data_dir.join("media_cache");
    Ok(list_media_folders(&cache))
}

fn list_media_folders(cache: &PathBuf) -> Vec<String> {
    let Ok(entries) = std::fs::read_dir(cache) else {
        return Vec::new();
    };
    let mut names: Vec<String> = entries
        .filter_map(|e| e.ok())
        .filter(|e| e.path().is_dir())
        .map(|e| e.file_name().to_string_lossy().to_string())
        .collect();
    names.sort_by_key(|n| n.to_lowercase());
    names
}

#[tauri::command]
pub fn list_project_media(
    state: State<'_, AppState>,
    niche: String,
    project_name: String,
) -> CmdResult<Vec<MediaCandidate>> {
    let (_db, rt) = ensure_runtime(&state, &niche)?;
    let cache = rt.settings.data_dir.join("media_cache");
    let catalog_path = catalog::catalog_path_for_data_dir(&rt.settings.data_dir);
    Ok(catalog::list_project_media_with_descriptions(&cache, &catalog_path, &project_name))
}

#[tauri::command]
pub fn register_media_description(
    state: State<'_, AppState>,
    niche: String,
    project_name: String,
    filename: String,
    description: String,
) -> CmdResult<catalog::CatalogEntry> {
    let (_db, rt) = ensure_runtime(&state, &niche)?;
    let catalog_path = catalog::catalog_path_for_data_dir(&rt.settings.data_dir);
    Ok(catalog::register_media(&catalog_path, &project_name, &filename, &description)?)
}

/// Copy a user-picked file into the project's media folder and return the
/// stored path. Uses `safe_filename` so a picked name cannot escape the
/// cache directory.
#[tauri::command]
pub fn import_media(
    state: State<'_, AppState>,
    niche: String,
    project_name: String,
    source_path: String,
) -> CmdResult<String> {
    let (_db, rt) = ensure_runtime(&state, &niche)?;

    let src = PathBuf::from(&source_path);
    if project_name.trim().is_empty() {
        return Err(CoreError::validation("Choose a project for imported media.").into());
    }
    let validation = if x_core::utils::files::is_video_path(&src) {
        x_core::utils::files::validate_video(&src)
    } else {
        x_core::utils::files::validate_image(&src)
    };
    if !validation.ok {
        return Err(CmdError { kind: "media".into(), message: validation.reason });
    }
    let filename = x_core::utils::files::safe_filename(
        &src.file_name().map(|n| n.to_string_lossy().to_string()).unwrap_or_default(),
    );
    let folder = media_library::project_media_dir(
        &rt.settings.data_dir.join("media_cache"),
        &project_name,
    );
    std::fs::create_dir_all(&folder).map_err(|e| CmdError {
        kind: "io".into(),
        message: format!("create {}: {e}", folder.display()),
    })?;
    // Never replace an existing library asset, including one used by a draft.
    let mut dest = folder.join(&filename);
    let mut suffix = 1;
    while dest.exists() {
        let stem = src.file_stem().unwrap_or_default().to_string_lossy();
        let ext = src.extension().unwrap_or_default().to_string_lossy();
        dest = folder.join(format!("{stem}-{suffix}.{ext}"));
        suffix += 1;
    }
    std::fs::copy(&src, &dest).map_err(|e| CmdError {
        kind: "io".into(),
        message: format!("copy to {}: {e}", dest.display()),
    })?;
    Ok(dest.to_string_lossy().to_string())
}

/// Grant preview access to one validated library asset. Videos use range
/// requests through Tauri's asset protocol, rather than base64 over IPC.
#[tauri::command]
pub fn prepare_media_preview(
    app: tauri::AppHandle,
    state: State<'_, AppState>,
    niche: String,
    path: String,
) -> CmdResult<String> {
    let (_db, rt) = ensure_runtime(&state, &niche)?;
    let cache = std::fs::canonicalize(rt.settings.data_dir.join("media_cache"))
        .map_err(|e| CoreError::Io(e.to_string()))?;
    let resolved = x_core::x::media::resolve_attachment(&rt.settings.data_dir, &path);
    let asset = std::fs::canonicalize(resolved).map_err(|e| CoreError::Io(e.to_string()))?;
    if !asset.starts_with(&cache) {
        return Err(CoreError::validation("Preview files must be in this workspace's media library.").into());
    }
    let validation = if x_core::utils::files::is_video_path(&asset) {
        x_core::utils::files::validate_video(&asset)
    } else {
        x_core::utils::files::validate_image(&asset)
    };
    if !validation.ok {
        return Err(CoreError::validation(validation.reason).into());
    }
    app.asset_protocol_scope().allow_file(&asset)
        .map_err(|e| CoreError::Io(e.to_string()))?;
    Ok(asset.to_string_lossy().to_string())
}

// ---- auth ------------------------------------------------------------------

/// Bind the callback before opening consent and retain the PKCE verifier
/// exclusively in the backend until `finish_auth` exchanges the code.
#[tauri::command]
pub fn begin_auth(app: tauri::AppHandle, state: State<'_, AppState>, niche: String) -> CmdResult<()> {
    let (_db, rt) = ensure_runtime(&state, &niche)?;
    if rt.settings.x.client_id.is_empty() {
        return Err(CoreError::Auth("X_CLIENT_ID is not configured in .env".into()).into());
    }
    let mut pending = state.pending_auth.lock();
    // A browser-open failure can leave an unconsumed session; retry replaces it.
    pending.take();
    let pkce = x_core::x::auth::new_pkce_pair();
    let csrf = x_core::x::auth::new_state();
    let port = rt.settings.x.callback_port;
    let port = u16::try_from(port).ok().filter(|p| *p != 0)
        .ok_or_else(|| CoreError::Auth("X_AUTH_CALLBACK_PORT must be between 1 and 65535".into()))?;
    let listener = std::net::TcpListener::bind(("127.0.0.1", port))
        .map_err(|_| CoreError::Auth(format!("Cannot listen on callback port {port}. Close other authorization helpers and try again.")))?;
    let redirect_uri = format!("http://127.0.0.1:{port}/callback");
    let url = x_core::x::auth::build_authorize_url(
        &rt.settings.x.client_id,
        &redirect_uri,
        x_core::x::auth::DEFAULT_SCOPES,
        &csrf,
        &pkce.code_challenge,
    );
    *pending = Some(PendingAuth {
        listener,
        verifier: pkce.code_verifier,
        csrf: csrf.clone(),
        redirect_uri: redirect_uri.clone(),
        runtime: rt,
    });
    if app.opener().open_url(url, None::<String>).is_err() {
        pending.take();
        return Err(CoreError::Auth("Could not open your browser. Check your default browser and try again.".into()).into());
    }
    Ok(())
}

#[tauri::command]
pub async fn finish_auth(state: State<'_, AppState>) -> CmdResult<()> {
    let pending = state.pending_auth.lock().take()
        .ok_or_else(|| CoreError::Auth("No authorization is pending. Click Authorize again.".into()))?;
    let PendingAuth { listener, verifier, csrf, redirect_uri, runtime } = pending;
    let code = tokio::task::spawn_blocking(move || {
        x_core::x::callback::wait_for_callback(listener, &csrf, std::time::Duration::from_secs(300))
    }).await.map_err(|_| CoreError::Auth("Authorization callback worker failed".into()))??;
    let http = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(30))
        .build().map_err(|_| CoreError::Auth("Could not initialize authorization client".into()))?;
    let mut bundle = x_core::x::auth::exchange_code(
        &http, &runtime.settings.x.client_id, &runtime.settings.x.client_secret,
        &code, &verifier, &redirect_uri,
    ).await?;
    if !bundle.scope.split_whitespace().any(|s| s == "tweet.write") {
        return Err(CoreError::Auth("X did not grant posting permission. Enable write access and authorize again.".into()).into());
    }
    bundle.bearer_token = runtime.settings.x.bearer_token.clone();
    // Shared tokens authorize both niches; update any legacy per-niche store too.
    let shared_path = runtime.settings.repo_root.join("data/oauth_tokens.json");
    if runtime.tokens.store_path() != shared_path {
        x_core::x::auth::TokenStore::new(&shared_path).save(&bundle)?;
    }
    runtime.tokens.save_initial(bundle)?;
    if let Some(current) = state.inner.lock().as_ref() {
        current.tokens.invalidate_cache();
    }
    Ok(())
}

#[tauri::command]
pub async fn identity(state: State<'_, AppState>, niche: String) -> CmdResult<Option<String>> {
    let (_db, rt) = ensure_runtime(&state, &niche)?;
    if !rt.tokens.store_path().exists() {
        return Ok(None);
    }
    // A failed call (expired token, no network) is not an error worth
    // surfacing here: the UI only wants to know whether we're authorized.
    Ok(rt.x.get_me().await.ok().map(|u| u.username))
}

/// Build the app state. Registration happens on the `Builder`, because
/// Tauri 2 removed `App::manage` and `App::plugin`.
pub fn new_state() -> AppState {
    AppState::new()
}

/// The command list, referenced from `lib.rs`'s `invoke_handler` so it
/// is declared in exactly one place.
#[macro_export]
macro_rules! command_list {
    () => {
        tauri::generate_handler![
            $crate::commands::bootstrap,
            $crate::commands::save_creators,
            $crate::commands::save_projects,
            $crate::commands::list_projects,
            $crate::commands::fetch_recent,
            $crate::commands::list_tweets,
            $crate::commands::set_tweet_statuses,
            $crate::commands::generate_draft,
            $crate::commands::save_draft,
            $crate::commands::list_drafts,
            $crate::commands::update_draft,
            $crate::commands::delete_draft,
            $crate::commands::preview_publish,
            $crate::commands::post_draft,
            $crate::commands::recent_history,
            $crate::commands::session_meter,
            $crate::commands::list_project_folders,
            $crate::commands::list_project_media,
            $crate::commands::register_media_description,
            $crate::commands::import_media,
            $crate::commands::prepare_media_preview,
            $crate::commands::begin_auth,
            $crate::commands::finish_auth,
            $crate::commands::identity,
        ]
    };
}
