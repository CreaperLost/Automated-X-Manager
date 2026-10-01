//! Project-folder organization for the local personal media library.
//!
//! Ported from `src/x_auto/utils/media_library.py`.

use std::path::{Path, PathBuf};

use regex::Regex;
use std::sync::OnceLock;

pub const SUPPORTED_IMAGE_SUFFIXES: &[&str] =
    &[".avif", ".gif", ".jpeg", ".jpg", ".png", ".webp"];
pub const SUPPORTED_VIDEO_SUFFIXES: &[&str] = &[".mov", ".mp4", ".webm"];

fn unsafe_folder_chars() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    // `[<>:"/\\|?*]` plus control characters.
    R.get_or_init(|| Regex::new(r#"[<>:"/\\|?*\x00-\x1f]"#).expect("valid regex"))
}

fn whitespace_run() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| Regex::new(r"\s+").expect("valid regex"))
}

/// A readable, cross-platform folder name for a project.
pub fn safe_project_folder_name(project_name: &str) -> String {
    let substituted = unsafe_folder_chars().replace_all(project_name.trim(), "-").to_string();
    let trimmed = substituted.trim_matches(['.', ' ']).to_string();
    let collapsed = whitespace_run().replace_all(&trimmed, " ").to_string();
    if collapsed.is_empty() {
        "Unnamed project".to_string()
    } else {
        collapsed
    }
}

/// Media directory for one project. Does not create it.
pub fn project_media_dir(cache_dir: &Path, project_name: &str) -> PathBuf {
    cache_dir.join(safe_project_folder_name(project_name))
}

/// Create and return the folder for every non-empty project name.
pub fn ensure_project_media_dirs(
    cache_dir: &Path,
    project_names: &[String],
) -> std::io::Result<Vec<(String, PathBuf)>> {
    std::fs::create_dir_all(cache_dir)?;
    let mut folders: Vec<(String, PathBuf)> = Vec::new();
    for raw_name in project_names {
        let name = raw_name.trim().to_string();
        if name.is_empty() || folders.iter().any(|(n, _)| *n == name) {
            continue;
        }
        let folder = project_media_dir(cache_dir, &name);
        std::fs::create_dir_all(&folder)?;
        folders.push((name, folder));
    }
    Ok(folders)
}

fn list_by_suffixes(folder: &Path, suffixes: &[&str]) -> Vec<PathBuf> {
    if !folder.is_dir() {
        return Vec::new();
    }
    let Ok(entries) = std::fs::read_dir(folder) else {
        return Vec::new();
    };
    let mut out: Vec<PathBuf> = entries
        .filter_map(|e| e.ok())
        .map(|e| e.path())
        .filter(|p| p.is_file())
        .filter(|p| {
            p.extension()
                .map(|e| {
                    let s = format!(".{}", e.to_string_lossy().to_lowercase());
                    suffixes.contains(&s.as_str())
                })
                .unwrap_or(false)
        })
        .collect();
    // Sort case-insensitively by file name, matching Python's `casefold` key.
    out.sort_by(|a, b| {
        let an = a.file_name().unwrap_or_default().to_string_lossy().to_lowercase();
        let bn = b.file_name().unwrap_or_default().to_string_lossy().to_lowercase();
        an.cmp(&bn)
    });
    out
}

/// Supported image files directly inside a media folder.
pub fn list_images(folder: &Path) -> Vec<PathBuf> {
    list_by_suffixes(folder, SUPPORTED_IMAGE_SUFFIXES)
}

/// Supported image and video files directly inside a media folder.
pub fn list_media(folder: &Path) -> Vec<PathBuf> {
    let mut all: Vec<&str> = SUPPORTED_IMAGE_SUFFIXES.to_vec();
    all.extend_from_slice(SUPPORTED_VIDEO_SUFFIXES);
    list_by_suffixes(folder, &all)
}

/// Supported video files directly inside a media folder.
pub fn list_videos(folder: &Path) -> Vec<PathBuf> {
    list_by_suffixes(folder, SUPPORTED_VIDEO_SUFFIXES)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn folder_names_are_cross_platform_safe() {
        assert_eq!(safe_project_folder_name("Hyperliquid"), "Hyperliquid");
        assert_eq!(safe_project_folder_name("TXFlow (code- TXAERO)"), "TXFlow (code- TXAERO)");
        assert_eq!(safe_project_folder_name("a/b\\c:d"), "a-b-c-d");
        assert_eq!(safe_project_folder_name("   "), "Unnamed project");
        assert_eq!(safe_project_folder_name("..."), "Unnamed project");
    }

    #[test]
    fn whitespace_collapses() {
        assert_eq!(safe_project_folder_name("a    b"), "a b");
    }

    #[test]
    fn ensure_dirs_skips_blank_and_duplicates() {
        let dir = tempfile::tempdir().unwrap();
        let names = vec!["A".to_string(), "".to_string(), "A".to_string(), "B".to_string()];
        let folders = ensure_project_media_dirs(dir.path(), &names).unwrap();
        assert_eq!(folders.len(), 2);
        assert!(folders[0].1.is_dir());
        assert!(folders[1].1.is_dir());
    }

    #[test]
    fn listing_filters_by_suffix_and_sorts_casefolded() {
        let dir = tempfile::tempdir().unwrap();
        for n in ["b.png", "A.PNG", "c.mp4", "notes.txt", "d.gif"] {
            std::fs::write(dir.path().join(n), b"x").unwrap();
        }
        let images = list_images(dir.path());
        let names: Vec<String> = images
            .iter()
            .map(|p| p.file_name().unwrap().to_string_lossy().to_string())
            .collect();
        assert_eq!(names, vec!["A.PNG", "b.png", "d.gif"]);

        let media = list_media(dir.path());
        assert_eq!(media.len(), 4);

        let videos = list_videos(dir.path());
        assert_eq!(videos.len(), 1);
    }

    #[test]
    fn listing_missing_folder_is_empty() {
        assert!(list_images(Path::new("/nope/nothing")).is_empty());
    }
}