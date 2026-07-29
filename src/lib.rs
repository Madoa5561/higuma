use std::{
    borrow::Cow,
    cmp::Reverse,
    collections::{HashMap, HashSet},
    fs,
    net::SocketAddr,
    path::{Path, PathBuf},
    sync::{Arc, Mutex, RwLock},
};

use axum::{
    body::{to_bytes, Body},
    extract::{
        ws::{CloseFrame, Message, WebSocket, WebSocketUpgrade},
        ConnectInfo, FromRequestParts, State,
    },
    http::{
        header::{HeaderName, HeaderValue, CONTENT_LENGTH, CONTENT_TYPE, SERVER},
        Request, StatusCode,
    },
    response::{IntoResponse, Response},
    routing::any,
    Router,
};
use futures_util::{SinkExt, StreamExt};
use minijinja::{path_loader, Environment};
use percent_encoding::percent_decode_str;
use pyo3::{
    exceptions::{PyRuntimeError, PyValueError},
    prelude::*,
    types::{PyAny, PyBytes, PyDict, PyList, PyTuple},
};
use tokio::sync::mpsc as tokio_mpsc;
use tokio::{net::TcpListener, runtime::Builder, signal};
use tokio_util::io::ReaderStream;
use tower_http::compression::CompressionLayer;
use url::form_urlencoded;

const DEFAULT_MAX_BODY_SIZE: usize = 8 * 1024 * 1024;

#[derive(Clone, Debug)]
enum Converter {
    String,
    Int,
    Float,
    Uuid,
    Path,
}

#[derive(Clone, Debug)]
enum RouteSegment {
    Static(String),
    Parameter { name: String, converter: Converter },
}

#[derive(Clone)]
struct RouteEntry {
    method: String,
    pattern: String,
    segments: Vec<RouteSegment>,
    specificity: usize,
    handler: Arc<Py<PyAny>>,
    preflight: Option<Arc<Py<PyAny>>>,
    allowed_origins: Vec<String>,
}

#[derive(Default)]
struct RouteTable {
    entries: Vec<RouteEntry>,
}

struct LookupResult {
    handler: Option<Arc<Py<PyAny>>>,
    params: HashMap<String, String>,
    pattern: Option<String>,
    allowed_methods: Vec<String>,
    preflight: Option<Arc<Py<PyAny>>>,
    allowed_origins: Vec<String>,
}

type WebSocketReceive = (
    String,
    Option<String>,
    Option<Vec<u8>>,
    Option<u16>,
    Option<String>,
);

type RouteMatch = (
    u8,
    Arc<Py<PyAny>>,
    HashMap<String, String>,
    String,
    Option<Arc<Py<PyAny>>>,
    Vec<String>,
);

enum IncomingMessage {
    Text(String),
    Binary(Vec<u8>),
    Close(u16, String),
}

enum OutgoingMessage {
    Text(String),
    Binary(Vec<u8>),
    Close(u16, String),
}

#[pyclass(name = "WebSocketSession")]
struct WebSocketSession {
    incoming: Arc<Mutex<tokio_mpsc::Receiver<IncomingMessage>>>,
    outgoing: tokio_mpsc::Sender<OutgoingMessage>,
}

#[pymethods]
impl WebSocketSession {
    fn receive(&self, py: Python<'_>) -> PyResult<WebSocketReceive> {
        let incoming = self.incoming.clone();
        let message = py.detach(move || {
            incoming
                .lock()
                .map_err(|_| "failed to lock WebSocket receiver".to_string())?
                .blocking_recv()
                .ok_or_else(|| "WebSocket connection closed".to_string())
        });
        match message {
            Ok(IncomingMessage::Text(value)) => {
                Ok(("text".to_string(), Some(value), None, None, None))
            }
            Ok(IncomingMessage::Binary(value)) => {
                Ok(("bytes".to_string(), None, Some(value), None, None))
            }
            Ok(IncomingMessage::Close(code, reason)) => {
                Ok(("close".to_string(), None, None, Some(code), Some(reason)))
            }
            Err(error) => Err(PyRuntimeError::new_err(error)),
        }
    }

    fn send_text(&self, py: Python<'_>, data: String) -> PyResult<()> {
        let outgoing = self.outgoing.clone();
        py.detach(move || outgoing.blocking_send(OutgoingMessage::Text(data)))
            .map_err(|_| PyRuntimeError::new_err("WebSocket connection closed"))
    }

    fn send_bytes(&self, py: Python<'_>, data: Vec<u8>) -> PyResult<()> {
        let outgoing = self.outgoing.clone();
        py.detach(move || outgoing.blocking_send(OutgoingMessage::Binary(data)))
            .map_err(|_| PyRuntimeError::new_err("WebSocket connection closed"))
    }

    #[pyo3(signature = (code = 1000, reason = "".to_string()))]
    fn close(&self, py: Python<'_>, code: u16, reason: String) -> PyResult<()> {
        let outgoing = self.outgoing.clone();
        py.detach(move || outgoing.blocking_send(OutgoingMessage::Close(code, reason)))
            .map_err(|_| PyRuntimeError::new_err("WebSocket connection closed"))
    }
}

impl RouteTable {
    fn add_route(
        &mut self,
        path: String,
        method: String,
        handler: Arc<Py<PyAny>>,
        preflight: Option<Arc<Py<PyAny>>>,
        allowed_origins: Vec<String>,
    ) -> Result<(), String> {
        let pattern = normalize_path(&path);
        let method_upper = method.to_ascii_uppercase();
        let (segments, specificity) = parse_route_pattern(&pattern)?;

        if self
            .entries
            .iter()
            .any(|entry| entry.method == method_upper && entry.pattern == pattern)
        {
            return Err(format!(
                "duplicate route registration: {method_upper} {pattern}"
            ));
        }

        self.entries.push(RouteEntry {
            method: method_upper,
            pattern,
            segments,
            specificity,
            handler,
            preflight,
            allowed_origins,
        });
        self.entries.sort_by_key(|entry| Reverse(entry.specificity));
        Ok(())
    }

    fn find_handler(&self, method: &str, path: &str) -> LookupResult {
        let normalized = normalize_path(path);
        let requested_method = method.to_ascii_uppercase();
        let mut allowed = HashSet::new();
        let mut match_result: Option<RouteMatch> = None;

        for entry in &self.entries {
            let Some(params) = match_segments(&entry.segments, &normalized) else {
                continue;
            };

            allowed.insert(entry.method.clone());
            if entry.method == "GET" {
                allowed.insert("HEAD".to_string());
            }

            let priority = if entry.method == requested_method {
                3
            } else if requested_method == "HEAD" && entry.method == "GET" {
                2
            } else if entry.method == "*" {
                1
            } else {
                0
            };
            if priority > 0
                && match_result
                    .as_ref()
                    .map(|current| priority > current.0)
                    .unwrap_or(true)
            {
                match_result = Some((
                    priority,
                    entry.handler.clone(),
                    params,
                    entry.pattern.clone(),
                    entry.preflight.clone(),
                    entry.allowed_origins.clone(),
                ));
            }
        }

        if !allowed.is_empty() {
            allowed.insert("OPTIONS".to_string());
        }
        let mut allowed_methods: Vec<_> = allowed.into_iter().collect();
        allowed_methods.sort();

        if let Some((_, handler, params, pattern, preflight, allowed_origins)) = match_result {
            LookupResult {
                handler: Some(handler),
                params,
                pattern: Some(pattern),
                allowed_methods,
                preflight,
                allowed_origins,
            }
        } else {
            LookupResult {
                handler: None,
                params: HashMap::new(),
                pattern: None,
                allowed_methods,
                preflight: None,
                allowed_origins: Vec::new(),
            }
        }
    }
}

#[derive(Clone)]
struct SharedState {
    routes: Arc<RwLock<RouteTable>>,
    websocket_routes: Arc<RwLock<RouteTable>>,
    fallback: Arc<RwLock<Option<Arc<Py<PyAny>>>>>,
    template_env: Arc<RwLock<Environment<'static>>>,
    max_body_size: usize,
    server_header: Arc<String>,
}

#[pyclass(name = "HigumaCore")]
struct HigumaCore {
    routes: Arc<RwLock<RouteTable>>,
    websocket_routes: Arc<RwLock<RouteTable>>,
    fallback: Arc<RwLock<Option<Arc<Py<PyAny>>>>>,
    template_env: Arc<RwLock<Environment<'static>>>,
    max_body_size: usize,
    server_header: String,
}

#[pymethods]
impl HigumaCore {
    #[new]
    #[pyo3(signature = (
        template_dir = "templates".to_string(),
        max_body_size = DEFAULT_MAX_BODY_SIZE,
        server_header = "higuma".to_string()
    ))]
    fn new(template_dir: String, max_body_size: usize, server_header: String) -> Self {
        let template_dir = PathBuf::from(template_dir);
        Self {
            routes: Arc::new(RwLock::new(RouteTable::default())),
            websocket_routes: Arc::new(RwLock::new(RouteTable::default())),
            fallback: Arc::new(RwLock::new(None)),
            template_env: Arc::new(RwLock::new(create_template_env(&template_dir))),
            max_body_size,
            server_header,
        }
    }

    fn add_websocket_route(
        &self,
        path: String,
        handler: Py<PyAny>,
        preflight: Py<PyAny>,
        allowed_origins: Vec<String>,
    ) -> PyResult<()> {
        self.websocket_routes
            .write()
            .map_err(|_| PyRuntimeError::new_err("failed to lock WebSocket route table"))?
            .add_route(
                path,
                "WEBSOCKET".to_string(),
                Arc::new(handler),
                Some(Arc::new(preflight)),
                allowed_origins,
            )
            .map_err(PyValueError::new_err)
    }

    fn add_route(&self, path: String, methods: Vec<String>, handler: Py<PyAny>) -> PyResult<()> {
        if methods.is_empty() {
            return Err(PyValueError::new_err("methods must not be empty"));
        }

        let mut routes = self
            .routes
            .write()
            .map_err(|_| PyRuntimeError::new_err("failed to lock route table"))?;
        let handler = Arc::new(handler);

        for method in methods {
            routes
                .add_route(path.clone(), method, handler.clone(), None, Vec::new())
                .map_err(PyValueError::new_err)?;
        }
        Ok(())
    }

    fn set_fallback(&self, handler: Py<PyAny>) -> PyResult<()> {
        let mut fallback = self
            .fallback
            .write()
            .map_err(|_| PyRuntimeError::new_err("failed to lock fallback handler"))?;
        *fallback = Some(Arc::new(handler));
        Ok(())
    }

    fn render_template(&self, template: String, context_json: String) -> PyResult<String> {
        render_template(&self.template_env, &template, &context_json)
            .map_err(PyRuntimeError::new_err)
    }

    fn clear_template_cache(&self) -> PyResult<()> {
        self.template_env
            .write()
            .map_err(|_| PyRuntimeError::new_err("failed to lock template cache"))?
            .clear_templates();
        Ok(())
    }

    #[pyo3(signature = (host = "127.0.0.1".to_string(), port = 8000, workers = 0))]
    fn run(&self, py: Python<'_>, host: String, port: u16, workers: usize) -> PyResult<()> {
        let worker_count = if workers == 0 {
            std::thread::available_parallelism()
                .map(|n| n.get())
                .unwrap_or(1)
        } else {
            workers
        };

        let addr: SocketAddr = format!("{host}:{port}")
            .parse()
            .map_err(|e| PyValueError::new_err(format!("invalid host/port: {e}")))?;

        let state = SharedState {
            routes: self.routes.clone(),
            websocket_routes: self.websocket_routes.clone(),
            fallback: self.fallback.clone(),
            template_env: self.template_env.clone(),
            max_body_size: self.max_body_size,
            server_header: Arc::new(self.server_header.clone()),
        };

        let runtime = Builder::new_multi_thread()
            .worker_threads(worker_count)
            .enable_all()
            .build()
            .map_err(|e| PyRuntimeError::new_err(format!("failed to create tokio runtime: {e}")))?;

        py.detach(move || {
            runtime.block_on(async move {
                let app = Router::new()
                    .fallback(any(dispatch))
                    .with_state(state)
                    .layer(CompressionLayer::new());
                let listener = TcpListener::bind(addr)
                    .await
                    .map_err(|e| PyRuntimeError::new_err(format!("failed to bind {addr}: {e}")))?;

                println!("higuma listening on http://{addr}");

                axum::serve(
                    listener,
                    app.into_make_service_with_connect_info::<SocketAddr>(),
                )
                .with_graceful_shutdown(shutdown_signal())
                .await
                .map_err(|e| PyRuntimeError::new_err(format!("server error: {e}")))
            })
        })
    }
}

async fn shutdown_signal() {
    let _ = signal::ctrl_c().await;
}

#[derive(Clone)]
struct WebSocketRequest {
    method: String,
    path: String,
    query: String,
    headers: HashMap<String, String>,
    raw_headers: Vec<(Vec<u8>, Vec<u8>)>,
    params: HashMap<String, String>,
    pattern: Option<String>,
    client_addr: String,
}

async fn handle_websocket(socket: WebSocket, callback: Arc<Py<PyAny>>, request: WebSocketRequest) {
    let (mut socket_sender, mut socket_receiver) = socket.split();
    let (incoming_tx, incoming_rx) = tokio_mpsc::channel(64);
    let (outgoing_tx, mut outgoing_rx) = tokio_mpsc::channel(64);

    tokio::task::spawn_blocking(move || {
        Python::attach(|py| -> PyResult<()> {
            let raw = websocket_request_to_dict(py, &request)?;
            let session = Py::new(
                py,
                WebSocketSession {
                    incoming: Arc::new(Mutex::new(incoming_rx)),
                    outgoing: outgoing_tx,
                },
            )?;
            callback.call1(py, (raw, session))?;
            Ok(())
        })
        .unwrap_or_else(|error| eprintln!("higuma WebSocket handler error: {error}"));
    });

    loop {
        tokio::select! {
            incoming = socket_receiver.next() => {
                match incoming {
                    Some(Ok(Message::Text(value))) => {
                        if incoming_tx.send(IncomingMessage::Text(value)).await.is_err() {
                            break;
                        }
                    }
                    Some(Ok(Message::Binary(value))) => {
                        if incoming_tx.send(IncomingMessage::Binary(value)).await.is_err() {
                            break;
                        }
                    }
                    Some(Ok(Message::Close(frame))) => {
                        let (code, reason) = frame
                            .map(|value| (value.code, value.reason.into_owned()))
                            .unwrap_or((1000, String::new()));
                        let _ = incoming_tx.send(IncomingMessage::Close(code, reason)).await;
                        break;
                    }
                    Some(Ok(Message::Ping(_))) | Some(Ok(Message::Pong(_))) => {}
                    Some(Err(_)) | None => {
                        let _ = incoming_tx.send(IncomingMessage::Close(
                            1006,
                            "connection closed".to_string(),
                        )).await;
                        break;
                    }
                }
            }
            outgoing = outgoing_rx.recv() => {
                let message = match outgoing {
                    Some(OutgoingMessage::Text(value)) => Message::Text(value),
                    Some(OutgoingMessage::Binary(value)) => Message::Binary(value),
                    Some(OutgoingMessage::Close(code, reason)) => Message::Close(
                        Some(CloseFrame { code, reason: Cow::Owned(reason) })
                    ),
                    None => Message::Close(Some(CloseFrame {
                        code: 1000,
                        reason: Cow::Borrowed("handler completed"),
                    })),
                };
                let is_close = matches!(message, Message::Close(_));
                if socket_sender.send(message).await.is_err() || is_close {
                    break;
                }
            }
        }
    }
}

async fn dispatch(
    State(state): State<SharedState>,
    ConnectInfo(peer_addr): ConnectInfo<SocketAddr>,
    req: Request<Body>,
) -> Response {
    let (mut parts, body) = req.into_parts();
    let websocket_upgrade = WebSocketUpgrade::from_request_parts(&mut parts, &state)
        .await
        .ok();
    let method = parts.method.as_str().to_ascii_uppercase();
    let path = normalize_path(parts.uri.path());
    let query = parts.uri.query().unwrap_or_default().to_string();
    let headers = parts.headers;

    if let Some(upgrade) = websocket_upgrade {
        let lookup = match state.websocket_routes.read() {
            Ok(routes) => routes.find_handler("WEBSOCKET", &path),
            Err(_) => {
                return finalize_response(
                    ResponsePayload::text(
                        "failed to lock WebSocket route table",
                        StatusCode::INTERNAL_SERVER_ERROR.as_u16(),
                    ),
                    &state,
                    false,
                )
            }
        };
        let Some(callback) = lookup.handler else {
            return finalize_response(
                ResponsePayload::text("WebSocket route not found", StatusCode::NOT_FOUND.as_u16()),
                &state,
                false,
            );
        };
        if !websocket_origin_allowed(&headers, &lookup.allowed_origins) {
            return finalize_response(
                ResponsePayload::text("WebSocket origin is not allowed", 403),
                &state,
                false,
            );
        }
        let request = WebSocketRequest {
            method,
            path,
            query,
            headers: headers
                .iter()
                .map(|(name, value)| {
                    (
                        name.as_str().to_string(),
                        value.to_str().unwrap_or_default().to_string(),
                    )
                })
                .collect(),
            raw_headers: headers
                .iter()
                .map(|(name, value)| (name.as_str().as_bytes().to_vec(), value.as_bytes().to_vec()))
                .collect(),
            params: lookup.params,
            pattern: lookup.pattern,
            client_addr: peer_addr.ip().to_string(),
        };
        if let Some(preflight) = lookup.preflight {
            let preflight_request = request.clone();
            let template_env = state.template_env.clone();
            let result = tokio::task::spawn_blocking(move || {
                Python::attach(|py| -> PyResult<ResponsePayload> {
                    let raw = websocket_request_to_dict(py, &preflight_request)?;
                    let py_obj = preflight.call1(py, (raw,))?;
                    py_to_response(py, py_obj.bind(py), &template_env)
                })
            })
            .await;
            let payload = match result {
                Ok(Ok(payload)) => payload,
                Ok(Err(error)) => {
                    eprintln!("higuma WebSocket preflight error: {error}");
                    ResponsePayload::text("Internal Server Error", 500)
                }
                Err(error) => {
                    eprintln!("higuma WebSocket preflight task failed: {error}");
                    ResponsePayload::text("Internal Server Error", 500)
                }
            };
            if payload.status >= 400 {
                return finalize_response(payload, &state, false);
            }
        }
        let upgrade = upgrade
            .max_message_size(state.max_body_size)
            .max_frame_size(state.max_body_size);
        return upgrade
            .on_upgrade(move |socket| handle_websocket(socket, callback, request))
            .into_response();
    }

    let body_bytes = match to_bytes(body, state.max_body_size).await {
        Ok(bytes) => bytes.to_vec(),
        Err(_) => {
            return finalize_response(
                ResponsePayload::text(
                    "request body too large",
                    StatusCode::PAYLOAD_TOO_LARGE.as_u16(),
                ),
                &state,
                false,
            )
        }
    };

    let lookup = match state.routes.read() {
        Ok(routes) => routes.find_handler(&method, &path),
        Err(_) => {
            return finalize_response(
                ResponsePayload::text(
                    "failed to lock route table",
                    StatusCode::INTERNAL_SERVER_ERROR.as_u16(),
                ),
                &state,
                method == "HEAD",
            )
        }
    };

    let route_status = if lookup.allowed_methods.is_empty() {
        StatusCode::NOT_FOUND
    } else {
        StatusCode::METHOD_NOT_ALLOWED
    };

    let callback = if let Some(handler) = lookup.handler {
        handler
    } else {
        match state.fallback.read() {
            Ok(fallback) => match fallback.as_ref() {
                Some(handler) => handler.clone(),
                None => {
                    let mut payload = ResponsePayload::text(
                        route_status.canonical_reason().unwrap_or("error"),
                        route_status.as_u16(),
                    );
                    if route_status == StatusCode::METHOD_NOT_ALLOWED {
                        payload
                            .headers
                            .push(("allow".to_string(), lookup.allowed_methods.join(", ")));
                    }
                    return finalize_response(payload, &state, method == "HEAD");
                }
            },
            Err(_) => {
                return finalize_response(
                    ResponsePayload::text(
                        "failed to lock fallback handler",
                        StatusCode::INTERNAL_SERVER_ERROR.as_u16(),
                    ),
                    &state,
                    method == "HEAD",
                )
            }
        }
    };

    let template_env = state.template_env.clone();
    let strip_body = method == "HEAD";
    let client_addr = peer_addr.ip().to_string();
    let params = lookup.params;
    let pattern = lookup.pattern;
    let allowed_methods = lookup.allowed_methods;
    let py_result = tokio::task::spawn_blocking(move || {
        Python::attach(|py| -> PyResult<ResponsePayload> {
            let request = PyDict::new(py);
            request.set_item("method", &method)?;
            request.set_item("path", &path)?;
            request.set_item("query_string", &query)?;
            request.set_item("query", query_to_dict(py, &query)?)?;
            request.set_item("headers", headers_to_dict(py, &headers)?)?;
            request.set_item("raw_headers", headers_to_list(py, &headers)?)?;
            request.set_item("body", PyBytes::new(py, &body_bytes))?;
            request.set_item("text", String::from_utf8_lossy(&body_bytes).to_string())?;
            request.set_item("path_params", params_to_dict(py, &params)?)?;
            request.set_item("route_pattern", pattern)?;
            request.set_item("route_error_status", route_status.as_u16())?;
            request.set_item("allowed_methods", allowed_methods)?;
            request.set_item("client_addr", client_addr)?;

            let py_obj = callback.call1(py, (request,))?;
            py_to_response(py, py_obj.bind(py), &template_env)
        })
    })
    .await;

    let payload = match py_result {
        Ok(Ok(payload)) => payload,
        Ok(Err(error)) => {
            eprintln!("higuma handler error: {error}");
            ResponsePayload::text("Internal Server Error", 500)
        }
        Err(error) => {
            eprintln!("higuma handler task failed: {error}");
            ResponsePayload::text("Internal Server Error", 500)
        }
    };

    finalize_response(payload, &state, strip_body)
}

fn finalize_response(
    mut payload: ResponsePayload,
    state: &SharedState,
    strip_body: bool,
) -> Response {
    let head_length = if strip_body {
        let length = Some(payload.body.length());
        payload.body = PayloadBody::Bytes(Vec::new());
        length
    } else {
        None
    };

    let mut response = payload.into_response();
    if let Some(length) = head_length {
        if let Ok(value) = HeaderValue::from_str(&length.to_string()) {
            response.headers_mut().insert(CONTENT_LENGTH, value);
        }
    }
    if !state.server_header.is_empty() && !response.headers().contains_key(SERVER) {
        if let Ok(value) = HeaderValue::from_str(&state.server_header) {
            response.headers_mut().insert(SERVER, value);
        }
    }
    response
}

fn query_to_dict<'py>(py: Python<'py>, query: &str) -> PyResult<Bound<'py, PyDict>> {
    let dict = PyDict::new(py);
    for (k, v) in form_urlencoded::parse(query.as_bytes()) {
        dict.set_item(k.as_ref(), v.as_ref())?;
    }
    Ok(dict)
}

fn websocket_request_to_dict<'py>(
    py: Python<'py>,
    request: &WebSocketRequest,
) -> PyResult<Bound<'py, PyDict>> {
    let raw = PyDict::new(py);
    raw.set_item("method", &request.method)?;
    raw.set_item("path", &request.path)?;
    raw.set_item("query_string", &request.query)?;
    raw.set_item("query", query_to_dict(py, &request.query)?)?;
    raw.set_item("headers", &request.headers)?;
    raw.set_item(
        "raw_headers",
        PyList::new(
            py,
            request.raw_headers.iter().map(|(name, value)| {
                (
                    PyBytes::new(py, name.as_slice()),
                    PyBytes::new(py, value.as_slice()),
                )
            }),
        )?,
    )?;
    raw.set_item("body", PyBytes::new(py, &[]))?;
    raw.set_item("text", "")?;
    raw.set_item("path_params", params_to_dict(py, &request.params)?)?;
    raw.set_item("route_pattern", &request.pattern)?;
    raw.set_item("route_error_status", 404)?;
    raw.set_item("allowed_methods", vec!["GET"])?;
    raw.set_item("client_addr", &request.client_addr)?;
    Ok(raw)
}

fn websocket_origin_allowed(
    headers: &axum::http::HeaderMap<HeaderValue>,
    allowed_origins: &[String],
) -> bool {
    let Some(origin) = headers.get("origin") else {
        return true;
    };
    let Ok(origin) = origin.to_str() else {
        return false;
    };
    if allowed_origins.iter().any(|value| value == "*") {
        return true;
    }
    if !allowed_origins.is_empty() {
        return allowed_origins
            .iter()
            .any(|value| value.trim_end_matches('/') == origin.trim_end_matches('/'));
    }

    let Some(host_header) = headers.get("host").and_then(|value| value.to_str().ok()) else {
        return false;
    };
    let Ok(origin_url) = url::Url::parse(origin) else {
        return false;
    };
    let Some(origin_host) = origin_url.host_str() else {
        return false;
    };
    let (host, port) = split_host_port(host_header);
    origin_host.eq_ignore_ascii_case(&host)
        && match port {
            Some(expected) => origin_url.port_or_known_default() == Some(expected),
            None => origin_url.port().is_none(),
        }
}

fn split_host_port(value: &str) -> (String, Option<u16>) {
    let value = value.trim();
    if let Some(rest) = value.strip_prefix('[') {
        if let Some((host, suffix)) = rest.split_once(']') {
            let port = suffix
                .strip_prefix(':')
                .and_then(|item| item.parse::<u16>().ok());
            return (host.to_string(), port);
        }
    }
    if value.matches(':').count() == 1 {
        if let Some((host, port)) = value.rsplit_once(':') {
            return (host.to_string(), port.parse::<u16>().ok());
        }
    }
    (value.to_string(), None)
}

fn headers_to_dict<'py>(
    py: Python<'py>,
    headers: &axum::http::HeaderMap<HeaderValue>,
) -> PyResult<Bound<'py, PyDict>> {
    let dict = PyDict::new(py);
    for (name, value) in headers.iter() {
        dict.set_item(name.as_str(), value.to_str().unwrap_or_default())?;
    }
    Ok(dict)
}

fn headers_to_list<'py>(
    py: Python<'py>,
    headers: &axum::http::HeaderMap<HeaderValue>,
) -> PyResult<Bound<'py, PyList>> {
    PyList::new(
        py,
        headers.iter().map(|(name, value)| {
            (
                PyBytes::new(py, name.as_str().as_bytes()),
                PyBytes::new(py, value.as_bytes()),
            )
        }),
    )
}

fn params_to_dict<'py>(
    py: Python<'py>,
    params: &HashMap<String, String>,
) -> PyResult<Bound<'py, PyDict>> {
    let dict = PyDict::new(py);
    for (name, value) in params {
        dict.set_item(name, value)?;
    }
    Ok(dict)
}

fn py_to_response(
    py: Python<'_>,
    obj: &Bound<'_, PyAny>,
    template_env: &Arc<RwLock<Environment<'static>>>,
) -> PyResult<ResponsePayload> {
    if is_truthy_marker(obj, "__higuma_file__")? {
        return file_response_from_py(obj);
    }

    if is_truthy_marker(obj, "__higuma_template__")? {
        let template: String = obj.getattr("template")?.extract()?;
        let context_json: String = obj.getattr("context_json")?.extract()?;
        let status: u16 = obj.getattr("status")?.extract()?;
        let headers = extract_response_headers(obj)?;
        let html = render_template(template_env, &template, &context_json)
            .map_err(PyRuntimeError::new_err)?;

        return Ok(ResponsePayload::new(
            html.into_bytes(),
            status,
            headers,
            Some("text/html; charset=utf-8".to_string()),
        ));
    }

    if is_truthy_marker(obj, "__higuma_response__")? {
        return response_from_py(py, obj);
    }

    if let Ok(tuple) = obj.cast::<PyTuple>() {
        return tuple_to_response(py, tuple);
    }

    let (body, content_type) = body_from_py(py, obj)?;
    Ok(ResponsePayload::new(body, 200, Vec::new(), content_type))
}

fn response_from_py(py: Python<'_>, obj: &Bound<'_, PyAny>) -> PyResult<ResponsePayload> {
    let status: u16 = obj.getattr("status_code")?.extract()?;
    let headers = extract_response_headers(obj)?;
    let media_type: Option<String> = obj.getattr("media_type")?.extract()?;
    let body_obj = obj.getattr("body")?;
    let (body, inferred_type) = body_from_py(py, &body_obj)?;
    Ok(ResponsePayload::new(
        body,
        status,
        headers,
        media_type.or(inferred_type),
    ))
}

fn file_response_from_py(obj: &Bound<'_, PyAny>) -> PyResult<ResponsePayload> {
    let path: String = obj.getattr("path")?.extract()?;
    let status: u16 = obj.getattr("status_code")?.extract()?;
    let headers = extract_response_headers(obj)?;
    let media_type: Option<String> = obj.getattr("media_type")?.extract()?;
    let file = fs::File::open(&path).map_err(|e| {
        PyRuntimeError::new_err(format!("failed to open response file {path}: {e}"))
    })?;
    let length = file
        .metadata()
        .map_err(|e| PyRuntimeError::new_err(format!("failed to stat response file {path}: {e}")))?
        .len();

    Ok(ResponsePayload::file(
        file,
        length,
        status,
        headers,
        media_type.or_else(|| Some("application/octet-stream".to_string())),
    ))
}

fn tuple_to_response(py: Python<'_>, tuple: &Bound<'_, PyTuple>) -> PyResult<ResponsePayload> {
    if tuple.len() != 2 && tuple.len() != 3 {
        return Err(PyValueError::new_err(
            "tuple response must be (body, status) or (body, status, headers)",
        ));
    }

    let body_obj = tuple.get_item(0)?;
    let status: u16 = tuple.get_item(1)?.extract()?;
    let headers = if tuple.len() == 3 {
        extract_headers(&tuple.get_item(2)?)?
    } else {
        Vec::new()
    };

    let (body, content_type) = body_from_py(py, &body_obj)?;
    Ok(ResponsePayload::new(body, status, headers, content_type))
}

fn body_from_py(py: Python<'_>, obj: &Bound<'_, PyAny>) -> PyResult<(Vec<u8>, Option<String>)> {
    if let Ok(text) = obj.extract::<String>() {
        return Ok((
            text.into_bytes(),
            Some("text/html; charset=utf-8".to_string()),
        ));
    }

    if let Ok(bytes) = obj.extract::<&[u8]>() {
        return Ok((bytes.to_vec(), Some("application/octet-stream".to_string())));
    }

    if obj.cast::<PyDict>().is_ok() || obj.cast::<PyList>().is_ok() {
        let json = py.import("json")?;
        let dumped: String = json.call_method1("dumps", (obj,))?.extract()?;
        return Ok((
            dumped.into_bytes(),
            Some("application/json; charset=utf-8".to_string()),
        ));
    }

    if obj.is_none() {
        return Ok((Vec::new(), None));
    }

    Ok((
        obj.str()?.to_string().into_bytes(),
        Some("text/plain; charset=utf-8".to_string()),
    ))
}

fn extract_headers(obj: &Bound<'_, PyAny>) -> PyResult<Vec<(String, String)>> {
    if obj.is_none() {
        return Ok(Vec::new());
    }

    if let Ok(dict) = obj.cast::<PyDict>() {
        let mut pairs = Vec::with_capacity(dict.len());
        for (key, value) in dict {
            pairs.push((key.str()?.to_string(), value.str()?.to_string()));
        }
        return Ok(pairs);
    }

    if let Ok(items) = obj.cast::<PyList>() {
        let mut pairs = Vec::with_capacity(items.len());
        for item in items {
            let tuple = item
                .cast::<PyTuple>()
                .map_err(|_| PyValueError::new_err("header items must be (name, value) tuples"))?;
            if tuple.len() != 2 {
                return Err(PyValueError::new_err(
                    "header items must be (name, value) tuples",
                ));
            }
            pairs.push((
                tuple.get_item(0)?.str()?.to_string(),
                tuple.get_item(1)?.str()?.to_string(),
            ));
        }
        return Ok(pairs);
    }

    Err(PyValueError::new_err(
        "headers must be a dict or a list of (name, value) tuples",
    ))
}

fn extract_response_headers(obj: &Bound<'_, PyAny>) -> PyResult<Vec<(String, String)>> {
    if obj.hasattr("header_items")? {
        extract_headers(&obj.getattr("header_items")?)
    } else {
        extract_headers(&obj.getattr("headers")?)
    }
}

fn is_truthy_marker(obj: &Bound<'_, PyAny>, marker: &str) -> PyResult<bool> {
    if !obj.hasattr(marker)? {
        return Ok(false);
    }
    obj.getattr(marker)?.extract::<bool>()
}

fn render_template(
    template_env: &Arc<RwLock<Environment<'static>>>,
    template_name: &str,
    context_json: &str,
) -> Result<String, String> {
    let context: serde_json::Value = serde_json::from_str(context_json)
        .map_err(|e| format!("invalid template context json: {e}"))?;
    let env = template_env
        .read()
        .map_err(|_| "failed to lock template cache".to_string())?;
    let template = env
        .get_template(template_name)
        .map_err(|e| format!("template loading failed: {e}"))?;
    template
        .render(context)
        .map_err(|e| format!("template render failed: {e}"))
}

fn create_template_env(template_dir: &Path) -> Environment<'static> {
    let mut env = Environment::new();
    env.set_loader(path_loader(template_dir));
    env
}

fn parse_route_pattern(pattern: &str) -> Result<(Vec<RouteSegment>, usize), String> {
    let parts = split_path(pattern);
    let mut segments = Vec::with_capacity(parts.len());
    let mut specificity = 0;

    for (index, part) in parts.iter().enumerate() {
        if part.starts_with('<') && part.ends_with('>') {
            let inner = &part[1..part.len() - 1];
            let (converter_name, name) = match inner.split_once(':') {
                Some((converter, name)) => (converter, name),
                None => ("string", inner),
            };

            if name.is_empty()
                || !name
                    .chars()
                    .all(|ch| ch == '_' || ch.is_ascii_alphanumeric())
            {
                return Err(format!("invalid route parameter name in {pattern}"));
            }

            let converter = match converter_name {
                "string" | "str" => Converter::String,
                "int" => Converter::Int,
                "float" => Converter::Float,
                "uuid" => Converter::Uuid,
                "path" => Converter::Path,
                _ => return Err(format!("unknown route converter: {converter_name}")),
            };

            if matches!(converter, Converter::Path) && index + 1 != parts.len() {
                return Err("<path:...> must be the final route segment".to_string());
            }

            specificity += match converter {
                Converter::String => 20,
                Converter::Int | Converter::Float | Converter::Uuid => 30,
                Converter::Path => 1,
            };
            segments.push(RouteSegment::Parameter {
                name: name.to_string(),
                converter,
            });
        } else if part.contains('<') || part.contains('>') {
            return Err(format!("invalid route pattern segment: {part}"));
        } else {
            specificity += 100;
            segments.push(RouteSegment::Static((*part).to_string()));
        }
    }

    Ok((segments, specificity))
}

fn match_segments(pattern: &[RouteSegment], path: &str) -> Option<HashMap<String, String>> {
    let path_parts = split_path(path);
    let mut params = HashMap::new();
    let mut path_index = 0;

    for segment in pattern {
        match segment {
            RouteSegment::Static(expected) => {
                let actual = path_parts.get(path_index)?;
                if decode_component(actual)? != *expected {
                    return None;
                }
                path_index += 1;
            }
            RouteSegment::Parameter { name, converter } => {
                if matches!(converter, Converter::Path) {
                    if path_index >= path_parts.len() {
                        return None;
                    }
                    let value = path_parts[path_index..]
                        .iter()
                        .map(|part| decode_component(part))
                        .collect::<Option<Vec<_>>>()?
                        .join("/");
                    if value.chars().any(char::is_control) {
                        return None;
                    }
                    params.insert(name.clone(), value);
                    path_index = path_parts.len();
                    continue;
                }

                let value = decode_component(path_parts.get(path_index)?)?;
                if value.contains('/')
                    || value.contains('\\')
                    || value.chars().any(char::is_control)
                {
                    return None;
                }
                let valid = match converter {
                    Converter::String => !value.is_empty(),
                    Converter::Int => value.parse::<i64>().is_ok(),
                    Converter::Float => value.parse::<f64>().is_ok(),
                    Converter::Uuid => is_uuid(&value),
                    Converter::Path => unreachable!(),
                };
                if !valid {
                    return None;
                }
                params.insert(name.clone(), value);
                path_index += 1;
            }
        }
    }

    if path_index == path_parts.len() {
        Some(params)
    } else {
        None
    }
}

fn is_uuid(value: &str) -> bool {
    value.len() == 36
        && value.chars().enumerate().all(|(index, ch)| {
            matches!(index, 8 | 13 | 18 | 23) && ch == '-'
                || !matches!(index, 8 | 13 | 18 | 23) && ch.is_ascii_hexdigit()
        })
}

fn split_path(path: &str) -> Vec<&str> {
    if path == "/" {
        Vec::new()
    } else {
        path.strip_prefix('/').unwrap_or(path).split('/').collect()
    }
}

fn decode_component(value: &str) -> Option<String> {
    percent_decode_str(value)
        .decode_utf8()
        .ok()
        .map(Cow::into_owned)
}

fn normalize_path(raw: &str) -> String {
    if raw.is_empty() || raw == "/" {
        return "/".to_string();
    }

    let mut path = if raw.starts_with('/') {
        raw.to_string()
    } else {
        format!("/{raw}")
    };

    while path.ends_with('/') && path.len() > 1 {
        path.pop();
    }
    path
}

enum PayloadBody {
    Bytes(Vec<u8>),
    File { file: fs::File, length: u64 },
}

impl PayloadBody {
    fn length(&self) -> u64 {
        match self {
            Self::Bytes(value) => value.len() as u64,
            Self::File { length, .. } => *length,
        }
    }
}

struct ResponsePayload {
    body: PayloadBody,
    status: u16,
    headers: Vec<(String, String)>,
    content_type: Option<String>,
}

impl ResponsePayload {
    fn new(
        body: Vec<u8>,
        status: u16,
        headers: Vec<(String, String)>,
        content_type: Option<String>,
    ) -> Self {
        Self {
            body: PayloadBody::Bytes(body),
            status,
            headers,
            content_type,
        }
    }

    fn file(
        file: fs::File,
        length: u64,
        status: u16,
        headers: Vec<(String, String)>,
        content_type: Option<String>,
    ) -> Self {
        Self {
            body: PayloadBody::File { file, length },
            status,
            headers,
            content_type,
        }
    }

    fn text(body: impl Into<String>, status: u16) -> Self {
        Self::new(
            body.into().into_bytes(),
            status,
            Vec::new(),
            Some("text/plain; charset=utf-8".to_string()),
        )
    }
}

impl IntoResponse for ResponsePayload {
    fn into_response(mut self) -> Response {
        let status = StatusCode::from_u16(self.status).unwrap_or(StatusCode::INTERNAL_SERVER_ERROR);
        if status == StatusCode::NO_CONTENT || status == StatusCode::NOT_MODIFIED {
            self.body = PayloadBody::Bytes(Vec::new());
        }
        let body_length = self.body.length();
        let body_is_empty = body_length == 0;
        let body = match self.body {
            PayloadBody::Bytes(value) => Body::from(value),
            PayloadBody::File { file, .. } => {
                Body::from_stream(ReaderStream::new(tokio::fs::File::from_std(file)))
            }
        };
        let mut response = Response::new(body);
        *response.status_mut() = status;
        if !body_is_empty && status != StatusCode::NO_CONTENT && status != StatusCode::NOT_MODIFIED
        {
            if let Ok(value) = HeaderValue::from_str(&body_length.to_string()) {
                response.headers_mut().insert(CONTENT_LENGTH, value);
            }
        }

        if let Some(content_type) = self.content_type {
            if let Ok(value) = HeaderValue::from_str(&content_type) {
                response.headers_mut().insert(CONTENT_TYPE, value);
            }
        }

        for (name, value) in self.headers {
            if let (Ok(header_name), Ok(header_value)) = (
                HeaderName::from_bytes(name.as_bytes()),
                HeaderValue::from_str(&value),
            ) {
                if header_name == CONTENT_LENGTH {
                    continue;
                }
                if header_name == CONTENT_TYPE {
                    response.headers_mut().insert(header_name, header_value);
                } else {
                    response.headers_mut().append(header_name, header_value);
                }
            } else {
                eprintln!("higuma dropped invalid response header: {name:?}");
            }
        }

        if status == StatusCode::NO_CONTENT || status == StatusCode::NOT_MODIFIED {
            response.headers_mut().remove(CONTENT_LENGTH);
        }
        response
    }
}

#[pymodule]
fn _core(_py: Python<'_>, module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_class::<HigumaCore>()?;
    module.add_class::<WebSocketSession>()?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn matches_typed_and_path_parameters() {
        let (segments, _) =
            parse_route_pattern("/users/<int:user_id>/files/<path:filename>").unwrap();
        let params = match_segments(&segments, "/users/42/files/docs/read%20me.txt").unwrap();

        assert_eq!(params.get("user_id").unwrap(), "42");
        assert_eq!(params.get("filename").unwrap(), "docs/read me.txt");
        assert!(match_segments(&segments, "/users/not-an-int/files/a.txt").is_none());
    }

    #[test]
    fn matches_uuid_parameters() {
        let (segments, _) = parse_route_pattern("/objects/<uuid:object_id>").unwrap();
        assert!(
            match_segments(&segments, "/objects/12345678-1234-5678-1234-567812345678").is_some()
        );
        assert!(match_segments(&segments, "/objects/invalid").is_none());
    }

    #[test]
    fn rejects_invalid_patterns() {
        assert!(parse_route_pattern("/files/<path:name>/metadata").is_err());
        assert!(parse_route_pattern("/users/<unknown:id>").is_err());
        assert!(parse_route_pattern("/users/<invalid-name>").is_err());
    }

    #[test]
    fn normalizes_trailing_slashes() {
        assert_eq!(normalize_path(""), "/");
        assert_eq!(normalize_path("users/"), "/users");
        assert_eq!(normalize_path("/users///"), "/users");
    }

    #[test]
    fn repeated_and_encoded_slashes_do_not_match_string_parameters() {
        let (segments, _) = parse_route_pattern("/values/<string:value>").unwrap();
        assert!(match_segments(&segments, "/values//12").is_none());
        assert!(match_segments(&segments, "/values/a%2Fb").is_none());
        assert!(match_segments(&segments, "/values/%FF").is_none());
    }

    #[test]
    fn websocket_origin_defaults_to_same_host() {
        let mut headers = axum::http::HeaderMap::new();
        headers.insert("host", HeaderValue::from_static("example.com"));
        headers.insert("origin", HeaderValue::from_static("https://example.com"));
        assert!(websocket_origin_allowed(&headers, &[]));

        headers.insert("origin", HeaderValue::from_static("https://evil.example"));
        assert!(!websocket_origin_allowed(&headers, &[]));
        assert!(websocket_origin_allowed(
            &headers,
            &["https://evil.example".to_string()]
        ));
    }
}
