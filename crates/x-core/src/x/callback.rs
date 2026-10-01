//! Loopback OAuth callback shared by the desktop app and setup helper.
use std::io::{BufRead, BufReader, Read, Write};
use std::net::TcpListener;
use std::time::{Duration, Instant};

use crate::error::{Error, Result};

fn parse_callback(target: &str, expected_state: &str) -> Result<Option<String>> {
    let url = url::Url::parse(&format!("http://127.0.0.1{target}"))
        .map_err(|_| Error::Auth("Invalid authorization callback".into()))?;
    if url.path() != "/callback" {
        return Ok(None);
    }
    let params: Vec<_> = url.query_pairs().collect();
    let value = |key: &str| {
        let mut matches = params.iter().filter(|(k, _)| k == key);
        let first = matches.next().map(|(_, v)| v.as_ref());
        if matches.next().is_some() {
            None
        } else {
            first
        }
    };
    if value("state") != Some(expected_state) {
        return Err(Error::Auth(
            "Authorization state mismatch. Try authorizing again.".into(),
        ));
    }
    if value("error").is_some() {
        return Err(Error::Auth(
            "X authorization was denied. Try authorizing again.".into(),
        ));
    }
    let code = value("code")
        .filter(|s| !s.is_empty())
        .ok_or_else(|| Error::Auth("Authorization callback is missing its code".into()))?;
    Ok(Some(code.to_string()))
}

/// Wait on a pre-bound listener. Run on a blocking worker, never the UI thread.
pub fn wait_for_callback(
    listener: TcpListener,
    expected_state: &str,
    timeout: Duration,
) -> Result<String> {
    listener
        .set_nonblocking(true)
        .map_err(|e| Error::Io(e.to_string()))?;
    let deadline = Instant::now() + timeout;
    while Instant::now() < deadline {
        let (mut stream, _) = match listener.accept() {
            Ok(connection) => connection,
            Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => {
                std::thread::sleep(Duration::from_millis(50));
                continue;
            }
            Err(e) => return Err(Error::Io(format!("Authorization callback: {e}"))),
        };
        let remaining = deadline.saturating_duration_since(Instant::now());
        if remaining.is_zero() {
            break;
        }
        stream
            .set_nonblocking(false)
            .map_err(|e| Error::Io(e.to_string()))?;
        let io_timeout = remaining.min(Duration::from_secs(2));
        stream
            .set_read_timeout(Some(io_timeout))
            .map_err(|e| Error::Io(e.to_string()))?;
        stream
            .set_write_timeout(Some(io_timeout))
            .map_err(|e| Error::Io(e.to_string()))?;
        let mut line = String::new();
        if BufReader::new((&stream).take(8192))
            .read_line(&mut line)
            .is_err()
        {
            continue;
        }
        let mut request = line.split_whitespace();
        let result = match (request.next(), request.next()) {
            (Some("GET"), Some(target)) => parse_callback(target, expected_state),
            _ => Ok(None),
        };
        let (status, message) = match &result {
            Ok(Some(_)) => (
                "200 OK",
                "Authorization received. Return to X-Automation to see the result.",
            ),
            Ok(None) => ("404 Not Found", "Not found."),
            Err(_) => (
                "400 Bad Request",
                "Authorization could not be completed. Return to X-Automation.",
            ),
        };
        let _ = write!(stream, "HTTP/1.1 {status}\r\nContent-Type: text/plain; charset=utf-8\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{message}", message.len());
        match result {
            Ok(Some(code)) => return Ok(code),
            Ok(None) => continue,
            Err(error) => return Err(error),
        }
    }
    Err(Error::Auth(
        "Authorization timed out. Click Authorize to try again.".into(),
    ))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn callback_accepts_either_parameter_order_and_decodes_code() {
        for target in [
            "/callback?code=a%2Bb%3D&state=expected",
            "/callback?state=expected&code=a%2Bb%3D",
        ] {
            assert_eq!(
                parse_callback(target, "expected").unwrap(),
                Some("a+b=".into())
            );
        }
    }

    #[test]
    fn callback_rejects_bad_state_denial_and_missing_code() {
        for target in [
            "/callback?code=a&state=wrong",
            "/callback?code=a",
            "/callback?state=expected&error=access_denied",
            "/callback?state=expected",
            "/callback?state=expected&state=wrong&code=a",
        ] {
            assert!(parse_callback(target, "expected").is_err());
        }
        assert_eq!(parse_callback("/favicon.ico", "expected").unwrap(), None);
    }

    #[test]
    fn listener_receives_callback_over_loopback() {
        let listener = TcpListener::bind(("127.0.0.1", 0)).unwrap();
        let address = listener.local_addr().unwrap();
        let sender = std::thread::spawn(move || {
            let mut stream = std::net::TcpStream::connect(address).unwrap();
            stream
                .write_all(
                    b"GET /callback?state=expected&code=test HTTP/1.1\r\nHost: localhost\r\n\r\n",
                )
                .unwrap();
            let mut response = String::new();
            // The server may close with unread request headers on Windows.
            let _ = stream.read_to_string(&mut response);
            assert!(response.starts_with("HTTP/1.1 200 OK"));
        });
        assert_eq!(
            wait_for_callback(listener, "expected", Duration::from_secs(3)).unwrap(),
            "test"
        );
        sender.join().unwrap();
    }

    #[test]
    fn callback_wait_times_out_without_consent() {
        let listener = TcpListener::bind(("127.0.0.1", 0)).unwrap();
        assert!(
            wait_for_callback(listener, "state", Duration::from_millis(10))
                .unwrap_err()
                .to_string()
                .contains("timed out")
        );
    }
}
