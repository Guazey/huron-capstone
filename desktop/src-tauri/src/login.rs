// The desktop half of the OAuth sign-in. The extension gets this from
// chrome.identity; a desktop app has to catch the redirect itself.
//
// Listen on localhost, send the user to Cognito's login page in their normal
// browser, and wait for Cognito to redirect back to /callback. The frontend
// checks `state` and exchanges the code with PKCE, so this only carries the URL.

use std::io::{self, ErrorKind, Read, Write};
use std::net::{Ipv4Addr, Ipv6Addr, TcpListener, TcpStream};
use std::sync::atomic::{AtomicBool, Ordering};
use std::thread::sleep;
use std::time::{Duration, Instant};

/// Cognito only redirects to exact registered URLs, so the fallbacks are a
/// fixed list. infra/login_setup.sh registers the same ports.
pub const PORTS: [u16; 3] = [47813, 47814, 47815];
const CALLBACK_PATH: &str = "/callback";
const TIMEOUT: Duration = Duration::from_secs(300);
pub const SIGNED_IN_PAGE: &str = "<!doctype html><title>Market Sidebar</title>\
<p style=\"font-family:system-ui;margin:3em;text-align:center\">\
Signed in. You can close this tab and go back to Market Sidebar.</p>";
pub const SIGNED_OUT_PAGE: &str = "<!doctype html><title>Market Sidebar</title>\
<p style=\"font-family:system-ui;margin:3em;text-align:center\">\
Signed out. You can close this tab.</p>";

static CANCELLED: AtomicBool = AtomicBool::new(false);

/// Stop a sign-in that is waiting for its redirect (the user gave up on it).
pub fn cancel() {
    CANCELLED.store(true, Ordering::SeqCst);
}

/// The redirect listener for one sign-in, bound before the login URL is
/// built so that URL can name the port that was actually free.
pub struct Listener {
    port: u16,
    sockets: Vec<TcpListener>,
}

impl Listener {
    /// Listen on the first of `ports` that is free.
    pub fn bind(ports: &[u16]) -> Result<Self, String> {
        let mut last_error = None;
        for &port in ports {
            match bind_loopback(port) {
                Ok(sockets) => return Ok(Self { port, sockets }),
                Err(e) => last_error = Some(e),
            }
        }
        Err(format!(
            "Couldn't listen for the sign-in redirect on ports {ports:?} ({}). \
             Another app may be using them.",
            last_error.map_or_else(|| "none given".into(), |e| e.to_string())
        ))
    }

    /// What to send Cognito as redirect_uri.
    pub fn redirect_uri(&self) -> String {
        format!("http://localhost:{}{CALLBACK_PATH}", self.port)
    }

    /// Run `open_login`, then return the URL Cognito redirected to, showing
    /// `page` in the browser tab that landed there.
    pub fn wait(
        self,
        page: &str,
        open_login: impl FnOnce() -> Result<(), String>,
    ) -> Result<String, String> {
        CANCELLED.store(false, Ordering::SeqCst);
        open_login()?;

        let deadline = Instant::now() + TIMEOUT;
        while Instant::now() < deadline {
            if CANCELLED.load(Ordering::SeqCst) {
                return Err("Sign-in was cancelled.".into());
            }
            for socket in &self.sockets {
                match socket.accept() {
                    Ok((stream, _)) => {
                        if let Some(target) = serve(stream, page) {
                            return Ok(format!("http://localhost:{}{target}", self.port));
                        }
                    }
                    // Nothing waiting yet, or a connection the browser dropped
                    // before we accepted it.
                    Err(e) if matches!(
                        e.kind(),
                        ErrorKind::WouldBlock
                            | ErrorKind::ConnectionAborted
                            | ErrorKind::ConnectionReset
                            | ErrorKind::Interrupted
                    ) => {}
                    Err(e) => return Err(e.to_string()),
                }
            }
            sleep(Duration::from_millis(100));
        }
        Err("Sign-in timed out. Try again.".into())
    }
}

/// Loopback only: nothing off this machine can reach the listener. Browsers
/// may resolve "localhost" to either address, so take IPv6 when available.
fn bind_loopback(port: u16) -> io::Result<Vec<TcpListener>> {
    let mut sockets = vec![TcpListener::bind((Ipv4Addr::LOCALHOST, port))?];
    // Skip IPv6 only on machines without it. If something else holds [::1]
    // on this port, the browser could hand it the redirect, so move on.
    match TcpListener::bind((Ipv6Addr::LOCALHOST, port)) {
        Ok(socket) => sockets.push(socket),
        Err(e) if e.kind() == ErrorKind::AddrInUse => return Err(e),
        Err(_) => {}
    }
    for socket in &sockets {
        socket.set_nonblocking(true)?;
    }
    Ok(sockets)
}

/// Answer one request. Returns its target if it was the callback.
fn serve(mut stream: TcpStream, page: &str) -> Option<String> {
    // Accepted sockets can inherit non-blocking mode; this one should wait.
    stream.set_nonblocking(false).ok()?;
    stream.set_read_timeout(Some(Duration::from_secs(5))).ok()?;
    let mut buf = [0u8; 8192];
    let n = stream.read(&mut buf).ok()?;
    let request = String::from_utf8_lossy(&buf[..n]);
    let target = callback_target(request.lines().next()?).map(str::to_owned);

    let (status, body) = match target {
        Some(_) => ("200 OK", page),
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
    fn falls_back_to_the_next_free_port() {
        let _taken = TcpListener::bind((Ipv4Addr::LOCALHOST, 47897)).unwrap();
        let listener = Listener::bind(&[47897, 47898]).unwrap();
        assert_eq!(listener.redirect_uri(), "http://localhost:47898/callback");
        assert!(Listener::bind(&[47897]).err().unwrap().contains("[47897]"));
    }

    #[test]
    fn returns_the_redirect_ignores_other_requests_and_cancels() {
        let port = 47899;
        let url = Listener::bind(&[port])
            .unwrap()
            .wait(SIGNED_IN_PAGE, || {
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

        // Same test, not a separate one: CANCELLED is shared, and cargo runs
        // tests in parallel.
        let err = Listener::bind(&[port])
            .unwrap()
            .wait(SIGNED_IN_PAGE, || {
                cancel();
                Ok(())
            })
            .unwrap_err();
        assert_eq!(err, "Sign-in was cancelled.");
    }
}
