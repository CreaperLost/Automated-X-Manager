//! Tauri application entrypoint for X-Automation.
//!
//! The window hosts a plain HTML/CSS/JS frontend (no build step, no
//! framework); `dist/` is loaded from disk. All behaviour lives in
//! [`commands`] and `x-core`.

pub mod commands;

use tracing_subscriber::EnvFilter;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tracing_subscriber::fmt()
        .with_env_filter(
            EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| EnvFilter::new("info,x_auto_lib=debug")),
        )
        .with_target(false)
        .init();

    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .manage(commands::new_state())
        .invoke_handler(crate::command_list!())
        .run(tauri::generate_context!())
        .expect("error while running X-Automation");
}