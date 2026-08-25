use serde::{Deserialize, Serialize};
use std::fs;
use std::io::{self, Read};
use std::path::{Path, PathBuf};

const PREFERENCES_FILE: &str = "desktop-storage.json";
const DATA_ROOT_MARKER: &str = ".loreweft-data-root";
const MIGRATION_BACKUP_DIR: &str = ".loreweft-migration-backup";
const EXCLUDED_RUNTIME_FILES: &[&str] = &[
    PREFERENCES_FILE,
    "backend.log",
    "startup.log",
    "backend.pid",
];

#[derive(Debug, Default, Clone, Serialize, Deserialize)]
#[serde(default, rename_all = "camelCase")]
struct StoredPreferences {
    creative_data_dir: Option<PathBuf>,
    cache_dir: Option<PathBuf>,
    pending_creative_data_dir: Option<PathBuf>,
    pending_cache_dir: Option<PathBuf>,
    migration_error: Option<String>,
}

#[derive(Debug, Clone)]
pub struct ActiveStorage {
    pub creative_data_dir: PathBuf,
    pub cache_dir: PathBuf,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct DesktopStorageSettings {
    pub creative_data_dir: String,
    pub cache_dir: String,
    pub pending_creative_data_dir: Option<String>,
    pub pending_cache_dir: Option<String>,
    pub default_creative_data_dir: String,
    pub default_cache_dir: String,
    pub restart_required: bool,
    pub migration_error: Option<String>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct DesktopStorageUpdate {
    pub creative_data_dir: String,
    pub cache_dir: String,
}

pub fn prepare_for_launch(default_dir: &Path) -> io::Result<ActiveStorage> {
    fs::create_dir_all(default_dir)?;
    let mut preferences = load_preferences(default_dir)?;
    let active_data = preferences
        .creative_data_dir
        .clone()
        .unwrap_or_else(|| default_dir.to_path_buf());
    let active_cache = preferences
        .cache_dir
        .clone()
        .unwrap_or_else(|| default_dir.to_path_buf());

    fs::create_dir_all(&active_data)?;
    fs::create_dir_all(&active_cache)?;

    let mut resolved_data = active_data.clone();
    let mut resolved_cache = active_cache.clone();
    let mut changed = false;

    if let Some(target) = preferences.pending_creative_data_dir.clone() {
        match migrate_creative_data(&active_data, &target) {
            Ok(()) => {
                resolved_data = target.clone();
                preferences.creative_data_dir = Some(target);
                preferences.pending_creative_data_dir = None;
                preferences.migration_error = None;
                changed = true;
            }
            Err(error) => {
                preferences.migration_error =
                    Some(format!("创作数据迁移失败，仍在使用原位置：{error}"));
                changed = true;
            }
        }
    }

    if let Some(target) = preferences.pending_cache_dir.clone() {
        match fs::create_dir_all(&target) {
            Ok(()) => {
                resolved_cache = target.clone();
                preferences.cache_dir = Some(target);
                preferences.pending_cache_dir = None;
                changed = true;
            }
            Err(error) => {
                preferences.migration_error =
                    Some(format!("缓存与日志目录无法启用，仍在使用原位置：{error}"));
                changed = true;
            }
        }
    }

    if changed {
        save_preferences(default_dir, &preferences)?;
    }

    fs::create_dir_all(&resolved_data)?;
    fs::create_dir_all(&resolved_cache)?;
    Ok(ActiveStorage {
        creative_data_dir: resolved_data,
        cache_dir: resolved_cache,
    })
}

pub fn read_settings(default_dir: &Path) -> io::Result<DesktopStorageSettings> {
    fs::create_dir_all(default_dir)?;
    let preferences = load_preferences(default_dir)?;
    Ok(to_public_settings(default_dir, &preferences))
}

pub fn update_settings(
    default_dir: &Path,
    update: DesktopStorageUpdate,
) -> Result<DesktopStorageSettings, String> {
    fs::create_dir_all(default_dir).map_err(|error| error.to_string())?;
    let mut preferences = load_preferences(default_dir).map_err(|error| error.to_string())?;

    let requested_data = validate_selected_directory(&update.creative_data_dir)?;
    let requested_cache = validate_selected_directory(&update.cache_dir)?;
    let active_data = preferences
        .creative_data_dir
        .clone()
        .unwrap_or_else(|| default_dir.to_path_buf());
    let active_cache = preferences
        .cache_dir
        .clone()
        .unwrap_or_else(|| default_dir.to_path_buf());

    if directory_is_within(&requested_data, &active_data) {
        return Err("新的创作数据目录不能放在当前创作数据目录内部".to_string());
    }
    if !same_directory(&requested_data, &requested_cache)
        && (directory_is_within(&requested_cache, &requested_data)
            || directory_is_within(&requested_data, &requested_cache))
    {
        return Err(
            "创作数据目录与缓存目录不能互相嵌套；可以选择同一目录或两个独立目录".to_string(),
        );
    }

    fs::create_dir_all(&requested_data)
        .map_err(|error| format!("无法创建创作数据目录 {}：{error}", requested_data.display()))?;
    fs::create_dir_all(&requested_cache).map_err(|error| {
        format!(
            "无法创建缓存与日志目录 {}：{error}",
            requested_cache.display()
        )
    })?;

    preferences.pending_creative_data_dir = if same_directory(&requested_data, &active_data) {
        None
    } else {
        Some(requested_data)
    };
    preferences.pending_cache_dir = if same_directory(&requested_cache, &active_cache) {
        None
    } else {
        Some(requested_cache)
    };
    preferences.migration_error = None;

    save_preferences(default_dir, &preferences).map_err(|error| error.to_string())?;
    Ok(to_public_settings(default_dir, &preferences))
}

fn to_public_settings(
    default_dir: &Path,
    preferences: &StoredPreferences,
) -> DesktopStorageSettings {
    let active_data = preferences
        .creative_data_dir
        .as_deref()
        .unwrap_or(default_dir);
    let active_cache = preferences.cache_dir.as_deref().unwrap_or(default_dir);
    DesktopStorageSettings {
        creative_data_dir: display_path(active_data),
        cache_dir: display_path(active_cache),
        pending_creative_data_dir: preferences
            .pending_creative_data_dir
            .as_deref()
            .map(display_path),
        pending_cache_dir: preferences.pending_cache_dir.as_deref().map(display_path),
        default_creative_data_dir: display_path(default_dir),
        default_cache_dir: display_path(default_dir),
        restart_required: preferences.pending_creative_data_dir.is_some()
            || preferences.pending_cache_dir.is_some(),
        migration_error: preferences.migration_error.clone(),
    }
}

fn preferences_path(default_dir: &Path) -> PathBuf {
    default_dir.join(PREFERENCES_FILE)
}

fn load_preferences(default_dir: &Path) -> io::Result<StoredPreferences> {
    let path = preferences_path(default_dir);
    if !path.is_file() {
        return Ok(StoredPreferences::default());
    }
    let raw = fs::read_to_string(path)?;
    match serde_json::from_str(&raw) {
        Ok(preferences) => Ok(preferences),
        Err(error) => {
            let backup = default_dir.join("desktop-storage.invalid.json");
            if !backup.exists() {
                let _ = fs::write(backup, raw);
            }
            Ok(StoredPreferences {
                migration_error: Some(format!("存储设置文件已损坏，已回退到默认目录：{error}")),
                ..StoredPreferences::default()
            })
        }
    }
}

fn save_preferences(default_dir: &Path, preferences: &StoredPreferences) -> io::Result<()> {
    fs::create_dir_all(default_dir)?;
    let destination = preferences_path(default_dir);
    let temporary = destination.with_extension(format!("json.tmp-{}", std::process::id()));
    let content = serde_json::to_vec_pretty(preferences)
        .map_err(|error| io::Error::new(io::ErrorKind::InvalidData, error))?;
    fs::write(&temporary, content)?;
    match fs::rename(&temporary, &destination) {
        Ok(()) => Ok(()),
        Err(error) if destination.exists() => {
            fs::remove_file(&destination)?;
            fs::rename(temporary, destination).map_err(|_| error)
        }
        Err(error) => Err(error),
    }
}

fn validate_selected_directory(value: &str) -> Result<PathBuf, String> {
    let trimmed = value.trim();
    if trimmed.is_empty() {
        return Err("存储目录不能为空".to_string());
    }
    let path = PathBuf::from(trimmed);
    if !path.is_absolute() {
        return Err("存储目录必须是绝对路径".to_string());
    }
    if path.is_file() {
        return Err(format!("{} 是文件，不是目录", path.display()));
    }
    Ok(path)
}

fn migrate_creative_data(source: &Path, target: &Path) -> io::Result<()> {
    if same_directory(source, target) {
        return Ok(());
    }
    fs::create_dir_all(source)?;
    fs::create_dir_all(target)?;
    let backup_dir = target.join(MIGRATION_BACKUP_DIR).join(format!(
        "{}-{}",
        env!("CARGO_PKG_VERSION"),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|value| value.as_millis())
            .unwrap_or_default()
    ));

    for entry in fs::read_dir(source)? {
        let entry = entry?;
        let name = entry.file_name();
        let name_text = name.to_string_lossy();
        if EXCLUDED_RUNTIME_FILES
            .iter()
            .any(|excluded| name_text.eq_ignore_ascii_case(excluded))
        {
            continue;
        }
        copy_entry_with_conflict_backup(&entry.path(), &target.join(name), &backup_dir)?;
    }

    fs::write(
        target.join(DATA_ROOT_MARKER),
        format!(
            "loreweft-data-root\nversion={}\n",
            env!("CARGO_PKG_VERSION")
        ),
    )?;
    Ok(())
}

fn copy_entry_without_overwrite(source: &Path, target: &Path) -> io::Result<()> {
    let metadata = fs::metadata(source)?;
    if metadata.is_dir() {
        fs::create_dir_all(target)?;
        for entry in fs::read_dir(source)? {
            let entry = entry?;
            copy_entry_without_overwrite(&entry.path(), &target.join(entry.file_name()))?;
        }
        return Ok(());
    }

    if target.exists() {
        let target_metadata = fs::metadata(target)?;
        if target_metadata.is_file()
            && target_metadata.len() == metadata.len()
            && files_equal(source, target)?
        {
            return Ok(());
        }
        return Err(io::Error::new(
            io::ErrorKind::AlreadyExists,
            format!("目标目录已有同名但内容规模不同的文件：{}", target.display()),
        ));
    }

    if let Some(parent) = target.parent() {
        fs::create_dir_all(parent)?;
    }
    let copied = fs::copy(source, target)?;
    if copied != metadata.len() {
        return Err(io::Error::new(
            io::ErrorKind::WriteZero,
            format!("文件复制校验失败：{}", source.display()),
        ));
    }
    Ok(())
}

fn copy_entry_with_conflict_backup(
    source: &Path,
    target: &Path,
    backup_dir: &Path,
) -> io::Result<()> {
    if !target.exists() {
        return copy_entry_without_overwrite(source, target);
    }
    let metadata = fs::metadata(source)?;
    if target.exists() {
        let target_metadata = fs::metadata(target)?;
        if target_metadata.is_file()
            && metadata.is_file()
            && target_metadata.len() == metadata.len()
            && files_equal(source, target)?
        {
            return Ok(());
        }
        let name = target.file_name().ok_or_else(|| {
            io::Error::new(
                io::ErrorKind::InvalidInput,
                "migration target has no file name",
            )
        })?;
        fs::create_dir_all(backup_dir)?;
        let backup_target = backup_dir.join(name);
        if backup_target.exists() {
            return Err(io::Error::new(
                io::ErrorKind::AlreadyExists,
                "migration backup already exists",
            ));
        }
        fs::rename(target, &backup_target)?;
    }

    if metadata.is_dir() {
        fs::create_dir_all(target)?;
        for entry in fs::read_dir(source)? {
            let entry = entry?;
            copy_entry_with_conflict_backup(
                &entry.path(),
                &target.join(entry.file_name()),
                backup_dir,
            )?;
        }
        return Ok(());
    }

    if let Some(parent) = target.parent() {
        fs::create_dir_all(parent)?;
    }
    let copied = fs::copy(source, target)?;
    if copied != metadata.len() {
        return Err(io::Error::new(
            io::ErrorKind::WriteZero,
            "migration copy verification failed",
        ));
    }
    Ok(())
}

fn files_equal(left: &Path, right: &Path) -> io::Result<bool> {
    let mut left_file = fs::File::open(left)?;
    let mut right_file = fs::File::open(right)?;
    let mut left_buffer = [0_u8; 64 * 1024];
    let mut right_buffer = [0_u8; 64 * 1024];
    loop {
        let left_read = left_file.read(&mut left_buffer)?;
        let right_read = right_file.read(&mut right_buffer)?;
        if left_read != right_read || left_buffer[..left_read] != right_buffer[..right_read] {
            return Ok(false);
        }
        if left_read == 0 {
            return Ok(true);
        }
    }
}

fn same_directory(left: &Path, right: &Path) -> bool {
    let left = normalize_for_comparison(left);
    let right = normalize_for_comparison(right);
    if cfg!(windows) {
        left.eq_ignore_ascii_case(&right)
    } else {
        left == right
    }
}

fn directory_is_within(candidate: &Path, parent: &Path) -> bool {
    if same_directory(candidate, parent) {
        return false;
    }
    let candidate = normalize_for_comparison(candidate);
    let parent = format!("{}\\", normalize_for_comparison(parent));
    if cfg!(windows) {
        candidate.to_lowercase().starts_with(&parent.to_lowercase())
    } else {
        candidate.starts_with(&parent)
    }
}

fn normalize_for_comparison(path: &Path) -> String {
    path.to_string_lossy()
        .trim_end_matches(['\\', '/'])
        .replace('/', "\\")
}

fn display_path(path: &Path) -> String {
    path.to_string_lossy().into_owned()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn temp_root(label: &str) -> PathBuf {
        std::env::temp_dir().join(format!(
            "loreweft-storage-{label}-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .expect("clock")
                .as_nanos()
        ))
    }

    #[test]
    fn update_defers_changes_until_next_launch() {
        let root = temp_root("defer");
        let default_dir = root.join("default");
        let target_data = root.join("creative");
        let target_cache = root.join("cache");
        fs::create_dir_all(&default_dir).expect("root");

        let settings = update_settings(
            &default_dir,
            DesktopStorageUpdate {
                creative_data_dir: display_path(&target_data),
                cache_dir: display_path(&target_cache),
            },
        )
        .expect("settings");

        assert!(settings.restart_required);
        assert_eq!(
            settings.pending_creative_data_dir.as_deref(),
            Some(display_path(&target_data).as_str())
        );
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn launch_copies_durable_data_and_keeps_runtime_logs_out() {
        let root = temp_root("migrate");
        let default_dir = root.join("default");
        let target_data = root.join("creative");
        let target_cache = root.join("cache");
        fs::create_dir_all(&default_dir).expect("root");
        fs::write(default_dir.join("loreweft.db"), b"database").expect("database");
        fs::write(default_dir.join("agent_settings.json"), b"settings").expect("settings");
        fs::write(default_dir.join("backend.log"), b"runtime").expect("log");

        update_settings(
            &default_dir,
            DesktopStorageUpdate {
                creative_data_dir: display_path(&target_data),
                cache_dir: display_path(&target_cache),
            },
        )
        .expect("settings");
        let active = prepare_for_launch(&default_dir).expect("launch");

        assert_eq!(active.creative_data_dir, target_data);
        assert_eq!(active.cache_dir, target_cache);
        assert!(active.creative_data_dir.join("loreweft.db").is_file());
        assert!(active
            .creative_data_dir
            .join("agent_settings.json")
            .is_file());
        assert!(!active.creative_data_dir.join("backend.log").exists());
        assert!(default_dir.join("loreweft.db").is_file());
        assert!(!read_settings(&default_dir).expect("read").restart_required);
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn rejects_a_target_nested_inside_the_active_data_directory() {
        let root = temp_root("nested");
        fs::create_dir_all(&root).expect("root");
        let result = update_settings(
            &root,
            DesktopStorageUpdate {
                creative_data_dir: display_path(&root.join("nested")),
                cache_dir: display_path(&root),
            },
        );
        assert!(result.is_err());
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn launch_preserves_conflicting_target_data_in_a_backup() {
        let root = temp_root("conflict");
        let default_dir = root.join("default");
        let target_data = root.join("creative");
        let target_cache = root.join("cache");
        fs::create_dir_all(&default_dir).expect("root");
        fs::create_dir_all(&target_data).expect("target");
        fs::write(default_dir.join("loreweft.db"), b"current database").expect("source");
        fs::write(target_data.join("loreweft.db"), b"older database").expect("target");

        update_settings(
            &default_dir,
            DesktopStorageUpdate {
                creative_data_dir: display_path(&target_data),
                cache_dir: display_path(&target_cache),
            },
        )
        .expect("settings");
        let active = prepare_for_launch(&default_dir).expect("launch");

        assert_eq!(active.creative_data_dir, target_data);
        assert_eq!(
            fs::read(target_data.join("loreweft.db")).expect("active db"),
            b"current database"
        );
        let backup_root = target_data.join(MIGRATION_BACKUP_DIR);
        assert!(backup_root.is_dir());
        let backup_db = fs::read_dir(backup_root)
            .expect("backup root")
            .filter_map(Result::ok)
            .map(|entry| entry.path().join("loreweft.db"))
            .find(|path| path.is_file())
            .expect("backup db");
        assert_eq!(
            fs::read(backup_db).expect("backup content"),
            b"older database"
        );
        let _ = fs::remove_dir_all(root);
    }
}
