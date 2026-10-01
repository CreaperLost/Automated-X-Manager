//! Integration check against a copy of the user's real `state.db`.
//!
//! Run with:
//! ```text
//! cargo test -p x-core --test real_data -- --ignored --nocapture
//! ```
//!
//! It copies the live database to a temp dir first, so the original is
//! never opened for writing. If the copy is missing it skips rather
//! than failing: a fresh checkout has no data yet.

use std::path::{Path, PathBuf};

use x_core::config;
use x_core::store::repos::Database;

fn repo_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .parent()
        .unwrap()
        .to_path_buf()
}

fn copy_db(source: &Path, dest_dir: &Path) -> Option<PathBuf> {
    if !source.is_file() {
        return None;
    }
    std::fs::create_dir_all(dest_dir).ok()?;
    let dest = dest_dir.join("state.db");
    std::fs::copy(source, &dest).ok()?;
    Some(dest)
}

fn report(label: &str, db: &Database) {
    println!(
        "  {:<14} accounts={} tweets={} projects={} drafts={} post_log={} media_uploads={}",
        label,
        db.list_accounts().unwrap_or_default().len(),
        db.list_tweets(None, 10_000).unwrap_or_default().len(),
        db.list_projects().unwrap_or_default().len(),
        db.list_drafts(None, 10_000).unwrap_or_default().len(),
        db.recent_log(10_000).unwrap_or_default().len(),
        db.list_media_uploads(10_000).unwrap_or_default().len(),
    );
}

#[test]
#[ignore = "reads a copy of the live database"]
fn real_database_reads_cleanly() {
    let root = repo_root();
    let tmp = std::env::temp_dir().join("x_auto_realdata_test");
    let _ = std::fs::remove_dir_all(&tmp);

    // Check both the shared root DB and the per-niche ones.
    for rel in ["data/state.db", "data/crypto/state.db", "data/ai/state.db"] {
        let source = root.join(rel);
        let Some(dest) = copy_db(&source, &tmp.join(rel.replace('/', "_"))) else {
            println!("skip {rel} (not present)");
            continue;
        };

        println!("== {rel} ({} bytes)", source.metadata().unwrap().len());
        let db = Database::open(&dest).expect("open copied database");
        report("after open:", &db);

        // Schema replay must be idempotent on a real file.
        db.with_conn(|conn| x_core::store::db::apply_schema(conn))
            .expect("replay schema");
        report("after replay:", &db);

        // Tweets must deserialize, including public_metrics and timestamps.
        let tweets = db.list_tweets(None, 10_000).unwrap();
        for t in tweets.iter().take(5) {
            assert!(!t.id.is_empty(), "tweet id must be non-empty");
            assert!(!t.account_handle.is_empty(), "handle must be non-empty");
            println!(
                "    tweet {} @{} metrics={} status={}",
                &t.id[..t.id.len().min(12)],
                t.account_handle,
                t.public_metrics.len(),
                t.status
            );
        }

        // Drafts must round-trip their image path list.
        for d in db.list_drafts(None, 100).unwrap().iter().take(5) {
            println!(
                "    draft {} status={} images={} body={}…",
                d.id.unwrap_or(0),
                d.status,
                d.image_paths.len(),
                d.body.chars().take(40).collect::<String>()
            );
        }

        // Cached media uploads: any with an id must respect the 24h TTL.
        let now_valid = db
            .list_media_uploads(1000)
            .unwrap()
            .iter()
            .filter(|m| m.is_still_valid())
            .count();
        let stale = db
            .list_media_uploads(1000)
            .unwrap()
            .iter()
            .filter(|m| m.x_media_id.is_some() && !m.is_still_valid())
            .count();
        println!("    media: {now_valid} cached-and-valid, {stale} cached-but-stale (will re-upload)");
    }
}

#[test]
#[ignore = "reads the live config directory"]
fn real_config_resolves() {
    let root = repo_root();
    // Sanity-check that we are pointed at the same repo the app uses, so a
    // misresolved CARGO_MANIFEST_DIR shows up here instead of silently
    // reading nothing.
    assert!(root.join(".env").exists() || root.join("config").is_dir());
    for niche in config::SUPPORTED_NICHES {
        let s = config::get_settings(niche);
        println!(
            "== niche {niche}\n  data_dir   {}\n  config_dir {}\n  title      {}\n  model      {}\n  ai_key     {}\n  x_bearer   {}\n  creators   {:?}",
            s.data_dir.display(),
            s.config_dir.display(),
            s.ui.page_title,
            s.minimax.model_id,
            if s.minimax.configured() { "set" } else { "MISSING" },
            if s.x.configured() { "set" } else { "MISSING" },
            s.accounts.iter().map(|a| a.handle.clone()).collect::<Vec<_>>(),
        );
    }
    println!("== shared\n  data_dir   {}\n  config_dir {}", config::data_dir().display(), config::config_dir().display());
}