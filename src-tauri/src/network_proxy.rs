const PROXY_ENV_NAMES: [&str; 6] = [
    "HTTPS_PROXY",
    "https_proxy",
    "ALL_PROXY",
    "all_proxy",
    "HTTP_PROXY",
    "http_proxy",
];

pub fn system_proxy() -> Option<String> {
    proxy_from_environment().or_else(windows_manual_proxy)
}

fn proxy_from_environment() -> Option<String> {
    PROXY_ENV_NAMES
        .iter()
        .filter_map(|name| std::env::var(name).ok())
        .find_map(|value| normalize_proxy_url(&value))
}

fn normalize_proxy_url(value: &str) -> Option<String> {
    let value = value.trim();
    if value.is_empty() {
        return None;
    }

    let candidate = if value.contains("://") {
        value.to_string()
    } else {
        format!("http://{value}")
    };
    let parsed = url::Url::parse(&candidate).ok()?;
    if !matches!(parsed.scheme(), "http" | "https")
        || parsed.host_str().is_none()
        || !parsed.username().is_empty()
        || parsed.password().is_some()
        || parsed.query().is_some()
        || parsed.fragment().is_some()
        || parsed.path() != "/"
    {
        return None;
    }

    Some(parsed.to_string().trim_end_matches('/').to_string())
}

fn proxy_from_windows_server(value: &str) -> Option<String> {
    let value = value.trim();
    if value.is_empty() {
        return None;
    }

    if !value.contains('=') {
        return normalize_proxy_url(value);
    }

    let entries: Vec<(&str, &str)> = value
        .split(';')
        .filter_map(|entry| entry.split_once('='))
        .map(|(scheme, address)| (scheme.trim(), address.trim()))
        .collect();

    ["https", "http"].iter().find_map(|wanted| {
        entries
            .iter()
            .find(|(scheme, _)| scheme.eq_ignore_ascii_case(wanted))
            .and_then(|(_, address)| normalize_proxy_url(address))
    })
}

#[cfg(windows)]
fn windows_manual_proxy() -> Option<String> {
    use winreg::enums::HKEY_CURRENT_USER;
    use winreg::RegKey;

    let current_user = RegKey::predef(HKEY_CURRENT_USER);
    let settings = current_user
        .open_subkey("Software\\Microsoft\\Windows\\CurrentVersion\\Internet Settings")
        .ok()?;
    let enabled: u32 = settings.get_value("ProxyEnable").ok()?;
    if enabled == 0 {
        return None;
    }
    let server: String = settings.get_value("ProxyServer").ok()?;
    proxy_from_windows_server(&server)
}

#[cfg(not(windows))]
fn windows_manual_proxy() -> Option<String> {
    None
}

#[cfg(test)]
mod tests {
    use super::{normalize_proxy_url, proxy_from_windows_server};

    #[test]
    fn normalizes_plain_host_and_port() {
        assert_eq!(
            normalize_proxy_url("127.0.0.1:7890").as_deref(),
            Some("http://127.0.0.1:7890")
        );
    }

    #[test]
    fn preserves_valid_http_proxy_url() {
        assert_eq!(
            normalize_proxy_url(" https://proxy.example.test:8443 ").as_deref(),
            Some("https://proxy.example.test:8443")
        );
    }

    #[test]
    fn prefers_https_target_proxy_from_windows_list() {
        assert_eq!(
            proxy_from_windows_server(
                "http=127.0.0.1:8080; HTTPS = proxy.example.test:8443; socks=127.0.0.1:1080"
            )
            .as_deref(),
            Some("http://proxy.example.test:8443")
        );
    }

    #[test]
    fn falls_back_to_http_target_proxy_from_windows_list() {
        assert_eq!(
            proxy_from_windows_server("http=127.0.0.1:8080;socks=127.0.0.1:1080").as_deref(),
            Some("http://127.0.0.1:8080")
        );
    }

    #[test]
    fn rejects_credentials_paths_and_unsupported_schemes() {
        assert_eq!(
            normalize_proxy_url("http://user:secret@proxy.test:8080"),
            None
        );
        assert_eq!(normalize_proxy_url("http://proxy.test:8080/path"), None);
        assert_eq!(normalize_proxy_url("socks5://127.0.0.1:1080"), None);
    }

    #[test]
    fn ignores_empty_or_socks_only_windows_proxy() {
        assert_eq!(proxy_from_windows_server(""), None);
        assert_eq!(proxy_from_windows_server("socks=127.0.0.1:1080"), None);
    }
}
