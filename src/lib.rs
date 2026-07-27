use std::{
    collections::{HashMap, HashSet},
    fs,
    net::SocketAddr,
    path::{Path, PathBuf},
    sync::{Arc, RwLock},
};

use axum::{
    body::{to_bytes, Body},
    extract::State,
    http::{
        header::{HeaderName, HeaderValue, CONTENT_LENGTH, CONTENT_TYPE, SERVER},
        Request, StatusCode,
    },
    response::{IntoResponse, Response},
    routing::any,
    Router,
};
use minijinja::{path_loader, Environment};
use percent_encoding::percent_decode_str;
use pyo3::{
    exceptions::{PyRuntimeError, PyValueError},
    prelude::*,
    types::{PyAny, PyBytes, PyDict, PyList, PyTuple},
};
use tokio::{net::TcpListener, runtime::Builder, signal};
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
}

impl RouteTable {
    fn add_route(
        &mut self,
        path: String,
        method: String,
        handler: Arc<Py<PyAny>>,
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
        });
        self.entries
            .sort_by(|a, b| b.specificity.cmp(&a.specificity));
        Ok(())
    }

    fn find_handler(&self, method: &str, path: &str) -> LookupResult {
        let normalized = normalize_path(path);
        let requested_method = method.to_ascii_uppercase();
        let mut allowed = HashSet::new();
        let mut match_result: Option<(u8, Arc<Py<PyAny>>, HashMap<String, String>, String)> = None;

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
                ));
            }
        }

        if !allowed.is_empty() {
            allowed.insert("OPTIONS".to_string());
        }
        let mut allowed_methods: Vec<_> = allowed.into_iter().collect();
        allowed_methods.sort();

        if let Some((_, handler, params, pattern)) = match_result {
            LookupResult {
                handler: Some(handler),
                params,
                pattern: Some(pattern),
                allowed_methods,
            }
        } else {
            LookupResult {
                handler: None,
                params: HashMap::new(),
                pattern: None,
                allowed_methods,
            }
        }
    }
}

#[derive(Clone)]
struct SharedState {
    routes: Arc<RwLock<RouteTable>>,
    fallback: Arc<RwLock<Option<Arc<Py<PyAny>>>>>,
    template_dir: Arc<PathBuf>,
    max_body_size: usize,
    server_header: Arc<String>,
}

#[pyclass(name = "HigumaCore")]
struct HigumaCore {
    routes: Arc<RwLock<RouteTable>>,
    fallback: Arc<RwLock<Option<Arc<Py<PyAny>>>>>,
    template_dir: PathBuf,
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
        Self {
            routes: Arc::new(RwLock::new(RouteTable::default())),
            fallback: Arc::new(RwLock::new(None)),
            template_dir: PathBuf::from(template_dir),
            max_body_size,
            server_header,
        }
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
                .add_route(path.clone(), method, handler.clone())
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
        render_template(&self.template_dir, &template, &context_json)
            .map_err(PyRuntimeError::new_err)
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
            fallback: self.fallback.clone(),
            template_dir: Arc::new(self.template_dir.clone()),
            max_body_size: self.max_body_size,
            server_header: Arc::new(self.server_header.clone()),
        };

        let runtime = Builder::new_multi_thread()
            .worker_threads(worker_count)
            .enable_all()
            .build()
            .map_err(|e| PyRuntimeError::new_err(format!("failed to create tokio runtime: {e}")))?;

        py.allow_threads(move || {
            runtime.block_on(async move {
                let app = Router::new().fallback(any(dispatch)).with_state(state);
                let listener = TcpListener::bind(addr)
                    .await
                    .map_err(|e| PyRuntimeError::new_err(format!("failed to bind {addr}: {e}")))?;

                println!("higuma listening on http://{addr}");

                axum::serve(listener, app)
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

async fn dispatch(State(state): State<SharedState>, req: Request<Body>) -> Response {
    let (parts, body) = req.into_parts();
    let method = parts.method.as_str().to_ascii_uppercase();
    let path = normalize_path(parts.uri.path());
    let query = parts.uri.query().unwrap_or_default().to_string();
    let headers = parts.headers;

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

    if method == "OPTIONS" && !lookup.allowed_methods.is_empty() && lookup.handler.is_none() {
        let mut payload = ResponsePayload::new(Vec::new(), 204, Vec::new(), None);
        payload
            .headers
            .push(("allow".to_string(), lookup.allowed_methods.join(", ")));
        return finalize_response(payload, &state, false);
    }

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

    let py_result = Python::with_gil(|py| -> PyResult<ResponsePayload> {
        let request = PyDict::new(py);
        request.set_item("method", &method)?;
        request.set_item("path", &path)?;
        request.set_item("query_string", &query)?;
        request.set_item("query", query_to_dict(py, &query)?)?;
        request.set_item("headers", headers_to_dict(py, &headers)?)?;
        request.set_item("body", PyBytes::new(py, &body_bytes))?;
        request.set_item("text", String::from_utf8_lossy(&body_bytes).to_string())?;
        request.set_item("path_params", params_to_dict(py, &lookup.params)?)?;
        request.set_item("route_pattern", lookup.pattern)?;
        request.set_item("route_error_status", route_status.as_u16())?;
        request.set_item("allowed_methods", lookup.allowed_methods.clone())?;

        let py_obj = callback.call1(py, (request,))?;
        py_to_response(py, py_obj.bind(py), &state.template_dir)
    });

    let payload = match py_result {
        Ok(payload) => payload,
        Err(err) => ResponsePayload::text(
            format!("handler error: {err}"),
            StatusCode::INTERNAL_SERVER_ERROR.as_u16(),
        ),
    };

    finalize_response(payload, &state, method == "HEAD")
}

fn finalize_response(
    mut payload: ResponsePayload,
    state: &SharedState,
    strip_body: bool,
) -> Response {
    if strip_body {
        let length = payload.body.len();
        payload
            .headers
            .push(("content-length".to_string(), length.to_string()));
        payload.body.clear();
    }

    let mut response = payload.into_response();
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
    template_dir: &Path,
) -> PyResult<ResponsePayload> {
    if is_truthy_marker(obj, "__higuma_file__")? {
        return file_response_from_py(obj);
    }

    if is_truthy_marker(obj, "__higuma_template__")? {
        let template: String = obj.getattr("template")?.extract()?;
        let context_json: String = obj.getattr("context_json")?.extract()?;
        let status: u16 = obj.getattr("status")?.extract()?;
        let headers = extract_response_headers(obj)?;
        let html = render_template(template_dir, &template, &context_json)
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

    if let Ok(tuple) = obj.downcast::<PyTuple>() {
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
    let body = fs::read(&path).map_err(|e| {
        PyRuntimeError::new_err(format!("failed to read response file {path}: {e}"))
    })?;

    Ok(ResponsePayload::new(
        body,
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

    if obj.downcast::<PyDict>().is_ok() || obj.downcast::<PyList>().is_ok() {
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

    if let Ok(dict) = obj.downcast::<PyDict>() {
        let mut pairs = Vec::with_capacity(dict.len());
        for (key, value) in dict {
            pairs.push((key.str()?.to_string(), value.str()?.to_string()));
        }
        return Ok(pairs);
    }

    if let Ok(items) = obj.downcast::<PyList>() {
        let mut pairs = Vec::with_capacity(items.len());
        for item in items {
            let tuple = item
                .downcast::<PyTuple>()
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
    template_dir: &Path,
    template_name: &str,
    context_json: &str,
) -> Result<String, String> {
    let mut env = Environment::new();
    env.set_loader(path_loader(template_dir));

    let context: serde_json::Value = serde_json::from_str(context_json)
        .map_err(|e| format!("invalid template context json: {e}"))?;
    let template = env
        .get_template(template_name)
        .map_err(|e| format!("template loading failed: {e}"))?;
    template
        .render(context)
        .map_err(|e| format!("template render failed: {e}"))
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
                if decode_component(actual) != *expected {
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
                        .collect::<Vec<_>>()
                        .join("/");
                    params.insert(name.clone(), value);
                    path_index = path_parts.len();
                    continue;
                }

                let value = decode_component(path_parts.get(path_index)?);
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
        path.trim_matches('/')
            .split('/')
            .filter(|part| !part.is_empty())
            .collect()
    }
}

fn decode_component(value: &str) -> String {
    percent_decode_str(value).decode_utf8_lossy().into_owned()
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

struct ResponsePayload {
    body: Vec<u8>,
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
            body,
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
    fn into_response(self) -> Response {
        let status = StatusCode::from_u16(self.status).unwrap_or(StatusCode::OK);
        let mut response = (status, self.body).into_response();

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
                if header_name == CONTENT_TYPE || header_name == CONTENT_LENGTH {
                    response.headers_mut().insert(header_name, header_value);
                } else {
                    response.headers_mut().append(header_name, header_value);
                }
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
}
