//! Project:Link loader backed by `data/<niche>/projects.csv`.
//!
//! Ported from `src/x_auto/ai/projects.py`.
//!
//! CSV format (header required, two columns):
//! ```text
//! name,url
//! ```
//! Empty lines and `#` comments are ignored. Malformed rows are skipped,
//! not raised — a bad row must not block publishing the rest.

use std::path::{Path, PathBuf};

use regex::Regex;
use std::sync::OnceLock;

use crate::config::Settings;
use crate::error::Result;
use crate::store::models::Project;
use crate::store::repos::Database;

fn url_re() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| Regex::new(r"(?i)^https?://\S+$").expect("valid regex"))
}

fn is_valid_url(value: &str) -> bool {
    if value.is_empty() || !url_re().is_match(value) {
        return false;
    }
    url::Url::parse(value)
        .map(|u| !u.scheme().is_empty() && !u.host_str().unwrap_or("").is_empty())
        .unwrap_or(false)
}

/// Default location for the project CSV.
pub fn csv_path(settings: &Settings) -> PathBuf {
    settings.data_dir.join("projects.csv")
}

/// Read a CSV file and return validated projects.
///
/// Missing file -> empty. Malformed rows are dropped with a warning.
pub fn load_csv(path: &Path) -> Vec<Project> {
    if !path.exists() {
        return Vec::new();
    }
    let Ok(content) = std::fs::read_to_string(path) else {
        return Vec::new();
    };

    let mut out: Vec<Project> = Vec::new();
    let mut seen: std::collections::HashSet<String> = std::collections::HashSet::new();
    // Column positions resolved from the header, not assumed to be 0 and 1.
    let mut name_col: Option<usize> = None;
    let mut url_col: Option<usize> = None;
    let mut have_header = false;

    for (idx, raw) in content.lines().enumerate() {
        let line_no = idx + 1;
        let line = raw.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let cells: Vec<String> = line.split(',').map(|c| c.trim().to_string()).collect();

        if !have_header {
            have_header = true;
            name_col = cells
                .iter()
                .position(|c| c.to_lowercase() == "name")
                .or(Some(0));
            url_col = cells
                .iter()
                .position(|c| c.to_lowercase() == "url")
                .or(Some(1));
            continue;
        }

        let name = name_col.and_then(|i| cells.get(i)).cloned().unwrap_or_default();
        let url = url_col.and_then(|i| cells.get(i)).cloned().unwrap_or_default();

        if name.is_empty() || url.is_empty() {
            eprintln!("[projects] line {line_no}: missing name or url, skipping");
            continue;
        }
        if !is_valid_url(&url) {
            eprintln!("[projects] line {line_no}: invalid url '{url}', skipping");
            continue;
        }
        if !seen.insert(name.clone()) {
            eprintln!("[projects] line {line_no}: duplicate name '{name}', skipping");
            continue;
        }
        out.push(Project { name, url, description: String::new(), tags: Vec::new() });
    }
    out
}

/// Write projects back to a two-column CSV, overwriting previous contents.
pub fn write_csv(path: &Path, projects: &[Project]) -> Result<()> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)
            .map_err(|e| crate::Error::Io(format!("create {}: {e}", parent.display())))?;
    }
    let mut out = String::from("name,url\n");
    for p in projects {
        out.push_str(&format!("{},{}\n", p.name.trim(), p.url.trim()));
    }
    std::fs::write(path, out)
        .map_err(|e| crate::Error::Io(format!("write {}: {e}", path.display())))?;
    Ok(())
}

/// Replace the `projects` table with the CSV contents. Idempotent.
pub fn sync_projects(settings: &Settings, db: &Database) -> Result<usize> {
    let mut path = csv_path(settings);
    if !path.exists() {
        let fallback = settings.repo_root.join("data").join("projects.csv");
        if fallback.exists() {
            path = fallback;
        }
    }
    let projects = load_csv(&path);
    db.replace_projects(&projects)?;
    Ok(projects.len())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn write(dir: &Path, body: &str) -> PathBuf {
        let p = dir.join("projects.csv");
        std::fs::write(&p, body).unwrap();
        p
    }

    #[test]
    fn parses_a_valid_csv() {
        let dir = tempfile::tempdir().unwrap();
        let p = write(
            dir.path(),
            "name,url\nAlpha,https://alpha.com\nBeta,https://beta.com/path\n",
        );
        let out = load_csv(&p);
        assert_eq!(out.len(), 2);
        assert_eq!(out[0].name, "Alpha");
        assert_eq!(out[1].url, "https://beta.com/path");
        assert!(out[0].tags.is_empty());
    }

    #[test]
    fn missing_file_is_empty() {
        assert!(load_csv(Path::new("/nope/nothing.csv")).is_empty());
    }

    #[test]
    fn skips_comments_and_blank_lines() {
        let dir = tempfile::tempdir().unwrap();
        let p = write(
            dir.path(),
            "# my projects\nname,url\n\nAlpha,https://a.com\n\n",
        );
        assert_eq!(load_csv(&p).len(), 1);
    }

    #[test]
    fn drops_invalid_urls_and_duplicates() {
        let dir = tempfile::tempdir().unwrap();
        let p = write(
            dir.path(),
            "name,url\nBad,not-a-url\nNoScheme,alpha.com\nDup,https://a.com\nDup,https://b.com\nGood,https://ok.com\n",
        );
        let out = load_csv(&p);
        // "Dup" is kept once (its first row) and dropped on the second.
        assert_eq!(out.len(), 2);
        let names: Vec<&str> = out.iter().map(|p| p.name.as_str()).collect();
        assert_eq!(names, vec!["Dup", "Good"]);
    }

    #[test]
    fn drops_rows_missing_a_field() {
        let dir = tempfile::tempdir().unwrap();
        let p = write(dir.path(), "name,url\nOnlyName\n,https://x.com\nGood,https://ok.com\n");
        let out = load_csv(&p);
        assert_eq!(out.len(), 1);
        assert_eq!(out[0].name, "Good");
    }

    #[test]
    fn url_validation_matches_the_rules() {
        assert!(is_valid_url("https://a.com"));
        assert!(is_valid_url("http://a.com/x?y=1"));
        assert!(!is_valid_url("ftp://a.com"));
        assert!(!is_valid_url("a.com"));
        assert!(!is_valid_url("https://"));
        assert!(!is_valid_url(""));
    }

    #[test]
    fn write_then_load_roundtrips() {
        let dir = tempfile::tempdir().unwrap();
        let p = dir.path().join("out.csv");
        let projects = vec![
            Project { name: "A".into(), url: "https://a.com".into(), description: String::new(), tags: vec!["t".into()] },
            Project { name: "B".into(), url: "https://b.com".into(), description: String::new(), tags: vec![] },
        ];
        write_csv(&p, &projects).unwrap();
        let body = std::fs::read_to_string(&p).unwrap();
        // Only two columns are written; description/tags are not persisted.
        assert!(body.starts_with("name,url\n"));
        assert!(!body.contains("description"));
        assert!(body.contains("A,https://a.com\n"));

        let back = load_csv(&p);
        assert_eq!(back.len(), 2);
        assert_eq!(back[0].name, "A");
    }

    #[test]
    fn sync_replaces_the_table_and_reports_the_count() {
        let dir = tempfile::tempdir().unwrap();
        let mut settings = crate::config::get_settings("crypto");
        settings.data_dir = dir.path().to_path_buf();
        std::fs::write(dir.path().join("projects.csv"), "name,url\nA,https://a.com\n")
            .unwrap();

        let db = Database::open_in_memory().unwrap();
        assert_eq!(sync_projects(&settings, &db).unwrap(), 1);
        assert_eq!(db.list_projects().unwrap().len(), 1);

        // Re-syncing is idempotent.
        assert_eq!(sync_projects(&settings, &db).unwrap(), 1);
        assert_eq!(db.list_projects().unwrap().len(), 1);
    }
}