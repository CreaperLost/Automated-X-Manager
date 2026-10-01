//! Centralized media catalog for project assets.
//!
//! Ported from `src/x_auto/utils/media_catalog.py`. Descriptions live in
//! `data/<niche>/media_catalog.json`, keyed by project then filename, so
//! media auto-matching has something to score against.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use chrono::Utc;
use serde::{Deserialize, Serialize};

use super::files::is_video_path;
use super::media_library::{list_media, project_media_dir};
use super::media_matching::MediaCandidate;

/// `data/<niche>/media_catalog.json`.
pub fn catalog_path_for_data_dir(data_dir: &Path) -> PathBuf {
    data_dir.join("media_catalog.json")
}

/// `data/<niche>/winners.json`.
pub fn winners_path(data_dir: &Path) -> PathBuf {
    data_dir.join("winners.json")
}

#[derive(Debug, Clone, Serialize, Deserialize, Default, PartialEq)]
pub struct CatalogEntry {
    #[serde(default)]
    pub description: String,
    #[serde(default)]
    pub added_at: String,
}

pub type Catalog = BTreeMap<String, BTreeMap<String, CatalogEntry>>;

/// Load the catalog. A missing or malformed file yields an empty catalog
/// rather than an error: descriptions are optional metadata.
pub fn load_catalog(path: &Path) -> Catalog {
    if !path.is_file() {
        return Catalog::new();
    }
    let Ok(raw) = std::fs::read_to_string(path) else {
        return Catalog::new();
    };
    let trimmed = raw.trim();
    if trimmed.is_empty() {
        return Catalog::new();
    }
    serde_json::from_str(trimmed).unwrap_or_default()
}

/// Save the catalog atomically (temp file + rename).
pub fn save_catalog(path: &Path, data: &Catalog) -> std::io::Result<()> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let body = serde_json::to_string_pretty(data).unwrap_or_else(|_| "{}".into());
    let tmp = path.with_extension("tmp");
    std::fs::write(&tmp, format!("{body}\n"))?;
    std::fs::rename(&tmp, path)
}

/// Register or update an asset's description.
pub fn register_media(
    catalog_path: &Path,
    project_name: &str,
    filename: &str,
    description: &str,
) -> crate::error::Result<CatalogEntry> {
    let clean_desc = description.trim();
    if clean_desc.is_empty() {
        return Err(crate::error::Error::validation("Media description is required."));
    }
    let clean_proj = project_name.trim();
    if clean_proj.is_empty() {
        return Err(crate::error::Error::validation("Project name is required."));
    }

    let mut catalog = load_catalog(catalog_path);
    let entry = CatalogEntry {
        description: clean_desc.to_string(),
        added_at: Utc::now().to_rfc3339(),
    };
    catalog.entry(clean_proj.to_string()).or_default().insert(filename.to_string(), entry.clone());
    save_catalog(catalog_path, &catalog)
        .map_err(|e| crate::error::Error::Io(format!("save catalog: {e}")))?;
    Ok(entry)
}

/// The logged description for an asset, or an empty string.
pub fn get_media_description(catalog_path: &Path, project_name: &str, filename: &str) -> String {
    load_catalog(catalog_path)
        .get(project_name.trim())
        .and_then(|m| m.get(filename))
        .map(|e| e.description.clone())
        .unwrap_or_default()
}

/// All supported media in a project's folder, with their descriptions.
pub fn list_project_media_with_descriptions(
    media_cache_dir: &Path,
    catalog_path: &Path,
    project_name: &str,
) -> Vec<MediaCandidate> {
    let folder = project_media_dir(media_cache_dir, project_name);
    let files = list_media(&folder);
    let catalog = load_catalog(catalog_path);
    let proj_entries = catalog.get(project_name.trim());

    files
        .into_iter()
        .map(|f| {
            let name = f.file_name().unwrap_or_default().to_string_lossy().to_string();
            let entry = proj_entries.and_then(|m| m.get(&name));
            MediaCandidate {
                filename: name,
                path: f.to_string_lossy().to_string(),
                description: entry.map(|e| e.description.clone()).unwrap_or_default(),
                added_at: entry.map(|e| e.added_at.clone()).unwrap_or_default(),
                is_video: is_video_path(&f),
                match_score: None,
            }
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn missing_catalog_is_empty() {
        let dir = tempfile::tempdir().unwrap();
        assert!(load_catalog(&dir.path().join("nope.json")).is_empty());
    }

    #[test]
    fn malformed_catalog_is_empty() {
        let dir = tempfile::tempdir().unwrap();
        let p = dir.path().join("bad.json");
        std::fs::write(&p, "{not json").unwrap();
        assert!(load_catalog(&p).is_empty());
    }

    #[test]
    fn register_requires_description_and_project() {
        let dir = tempfile::tempdir().unwrap();
        let p = dir.path().join("c.json");
        assert!(register_media(&p, "A", "a.png", "   ").is_err());
        assert!(register_media(&p, "  ", "a.png", "desc").is_err());
        assert!(register_media(&p, "A", "a.png", "desc").is_ok());
    }

    #[test]
    fn register_then_read_back() {
        let dir = tempfile::tempdir().unwrap();
        let p = dir.path().join("c.json");
        register_media(&p, "Alpha", "chart.png", "a price chart").unwrap();
        register_media(&p, "Alpha", "chart.png", "updated chart").unwrap();
        assert_eq!(
            get_media_description(&p, "Alpha", "chart.png"),
            "updated chart"
        );
        assert_eq!(get_media_description(&p, "Alpha", "missing.png"), "");
        assert_eq!(get_media_description(&p, "Other", "chart.png"), "");
    }

    #[test]
    fn lists_project_media_with_descriptions_and_video_flag() {
        let dir = tempfile::tempdir().unwrap();
        let cache = dir.path().join("media_cache");
        let proj = cache.join("Alpha");
        std::fs::create_dir_all(&proj).unwrap();
        std::fs::write(proj.join("a.png"), b"x").unwrap();
        std::fs::write(proj.join("b.mp4"), b"x").unwrap();
        std::fs::write(proj.join("c.txt"), b"x").unwrap();

        let catalog = dir.path().join("catalog.json");
        register_media(&catalog, "Alpha", "a.png", "described image").unwrap();

        let items = list_project_media_with_descriptions(&cache, &catalog, "Alpha");
        assert_eq!(items.len(), 2);
        let a = items.iter().find(|i| i.filename == "a.png").unwrap();
        assert_eq!(a.description, "described image");
        assert!(!a.is_video);
        let b = items.iter().find(|i| i.filename == "b.mp4").unwrap();
        assert!(b.is_video);
        assert_eq!(b.description, "");
    }
}