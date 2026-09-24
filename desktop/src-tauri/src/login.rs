// The desktop half of the OAuth sign-in. The extension gets this from
// chrome.identity; a desktop app has to catch the redirect itself.
//
// Listen on localhost, send the user to Cognito's login page in their normal
// browser, and wait for Cognito to redirect back to /callback. The frontend
// checks `state` and exchanges the code with PKCE, so this only carries the URL.

use std::io::{ErrorKind, Read, Write};
use std::net::{Ipv4Addr, Ipv6Addr, TcpListener, TcpStream};
use std::thread::sleep;
use std::time::{Duration, Instant};

const CALLBACK_PATH: &str = "/callback";
const TIMEOUT: Duration = Duration::from_secs(300);
const DONE_PAGE: &str = "<!doctype html><title>Market Sidebar</title>\
<p style=\"font-family:system-ui;margin:3em;text-align:center\">\
Signed in. You can close this tab and go back to Market Sidebar.</p>";

/// Bind the callback port, run `open_login`, and return the redirect URL.
pub fn wait_for_redirect(
    port: u16,
    open_login: impl FnOnce() -> Result<(), String>,
) -> Result<String, String> {
    // Loopback only: nothing off this machine can reach the listener. Browsers
    // may resolve "localhost" to either address, so take IPv6 when available.
    let mut listeners = vec![TcpListener::bind((Ipv4Addr::LOCALHOST, port)).map_err(|e| {
        format!("Couldn't listen on port {port} for the sign-in redirect ({e}). Is another sign-in open?")
    })?];
    listeners.extend(TcpListener::bind((Ipv6Addr::LOCALHOST, port)).ok());
    for listener in &listeners {
        listener.set_nonblocking(true).map_err(|e| e.to_string())?;
    }

    open_login()?;

    let deadline = Instant::now() + TIMEOUT;
    while Instant::now() < deadline {
        for listener in &listeners {
            match listener.accept() {
                Ok((stream, _)) => {
                    if let Some(target) = serve(stream) {
                        return Ok(format!("http://localhost:{port}{target}"));
                    }
                }
                Err(e) if e.kind() == ErrorKind::WouldBlock => {}
                Err(e) => return Err(e.to_string()),
            }
        }
        sleep(Duration::from_millis(100));
    }
    Err("Sign-in timed out. Try again.".into())
}

/// Answer one request. Returns its target if it was the callback.
fn serve(mut stream: TcpStream) -> Option<String> {
    // Accepted sockets can inherit non-blocking mode; this one should wait.
    stream.set_nonblocking(false).ok()?;
    stream.set_read_timeout(Some(Duration::from_secs(5))).ok()?;
    let mut buf = [0u8; 8192];
    let n = stream.read(&mut buf).ok()?;
    let request = String::from_utf8_lossy(&buf[..n]);
    let target = callback_target(request.lines().next()?).map(str::to_owned);

    let (status, body) = match target {
        Some(_) => ("200 OK", DONE_PAGE),
        None => ("404 Not Found", ""),
    };
    let _ = write!(
        stream,
        "HTTP/1.1 {status}\r\nContent-Type: text/html; charset=utf-8\r\n\
         Content-Length: {}\r\nConnection: close\r\n\r\n{body}",
        body.len()
    );
    target
}

/// The target of `GET /callback?... HTTP/1.1`, or None for any other request.
fn callback_target(request_line: &str) -> Option<&str> {
    let mut parts = request_line.split_whitespace();
    let (method, target) = (parts.next()?, parts.next()?);
    let path = target.split('?').next()?;
    (method == "GET" && path == CALLBACK_PATH).then_some(target)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn accepts_only_the_callback() {
        assert_eq!(
            callback_target("GET /callback?code=abc&state=xyz HTTP/1.1"),
            Some("/callback?code=abc&state=xyz")
        );
        assert_eq!(callback_target("GET /callback HTTP/1.1"), Some("/callback"));
        assert_eq!(callback_target("GET /favicon.ico HTTP/1.1"), None);
        assert_eq!(callback_target("GET /callbackx?code=1 HTTP/1.1"), None);
        assert_eq!(callback_target("POST /callback HTTP/1.1"), None);
        assert_eq!(callback_target(""), None);
    }

    #[test]
    fn returns_the_redirect_and_ignores_other_requests() {
        let port = 47899;
        let url = wait_for_redirect(port, || {
            std::thread::spawn(move || {
                for path in ["/favicon.ico", "/callback?code=abc&state=xyz"] {
                    let mut s = TcpStream::connect((Ipv4Addr::LOCALHOST, port)).unwrap();
                    write!(s, "GET {path} HTTP/1.1\r\nHost: localhost\r\n\r\n").unwrap();
                    let mut reply = String::new();
                    s.read_to_string(&mut reply).unwrap();
                }
            });
            Ok(())
        })
        .unwrap();
        assert_eq!(url, "http://localhost:47899/callback?code=abc&state=xyz");
    }
}
