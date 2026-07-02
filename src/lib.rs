use std::{
    collections::{HashMap, HashSet},
    net::SocketAddr,
    path::{Path, PathBuf},
    sync::{Arc, RwLock},
};

use axum::{
    body::{to_bytes, Body},
    extract::State,
    http::{
        header::{HeaderName, HeaderValue, CONTENT_TYPE},
        Request, StatusCode,
    },
    response::{IntoResponse, Response},
    routing::any,
    Router,
};
use minijinja::{path_loader, Environment};
use pyo3::{
    exceptions::{PyRuntimeError, PyValueError},
    prelude::*,
    types::{PyAny, PyBytes, PyDict, PyTuple},
};
use tokio::{net::TcpListener, runtime::Builder, signal};
use url::form_urlencoded;

const MAX_BODY_SIZE: usize = 8 * 1024 * 1024;

#[derive(Default)]
struct RouteTable {
    handlers: HashMap<(String, String), Py<PyAny>>,
    path_methods: HashMap<String, HashSet<String>>,
}

struct LookupResult {
    handler: Option<Py<PyAny>>,
    path_exists: bool,
}

impl RouteTable {
    fn add_route(&mut self, path: String, method: String, handler: Py<PyAny>) {
        let normalized_path = normalize_path(&path);
        let method_upper = method.to_ascii_uppercase();
        self.handlers
            .insert((method_upper.clone(), normalized_path.clone()), handler);
        self.path_methods
            .entry(normalized_path)
            .or_default()
            .insert(method_upper);
    }

    fn find_handler(&self, method: &str, path: &str) -> LookupResult {
        let method_upper = method.to_ascii_uppercase();
        let path_norm = normalize_path(path);

        if let Some(handler) = self
            .handlers
            .get(&(method_upper.clone(), path_norm.clone()))
        {
            return LookupResult {
                handler: Some(handler.clone()),
                path_exists: true,
            };
        }

        if let Some(handler) = self.handlers.get(&("*".to_string(), path_norm.clone())) {
            return LookupResult {
                handler: Some(handler.clone()),
                path_exists: true,
            };
        }

        LookupResult {
            handler: None,
            path_exists: self.path_methods.contains_key(&path_norm),
        }
    }
}

#[derive(Clone)]
struct SharedState {
    routes: Arc<RwLock<RouteTable>>,
    template_dir: Arc<PathBuf>,
}

#[pyclass(name = "HigumaCore")]
struct HigumaCore {
    routes: Arc<RwLock<RouteTable>>,
    template_dir: PathBuf,
}

#[pymethods]
impl HigumaCore {
    #[new]
    #[pyo3(signature = (template_dir = "templates".to_string()))]
    fn new(template_dir: String) -> Self {
        Self {
            routes: Arc::new(RwLock::new(RouteTable::default())),
            template_dir: PathBuf::from(template_dir),
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

        for method in methods {
            routes.add_route(path.clone(), method, handler.clone());
        }
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
            template_dir: Arc::new(self.template_dir.clone()),
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

    let body_bytes = match to_bytes(body, MAX_BODY_SIZE).await {
        Ok(bytes) => bytes.to_vec(),
        Err(_) => return (StatusCode::PAYLOAD_TOO_LARGE, "request body too large").into_response(),
    };

    let lookup = match state.routes.read() {
        Ok(routes) => routes.find_handler(&method, &path),
        Err(_) => {
            return (
                StatusCode::INTERNAL_SERVER_ERROR,
                "failed to lock route table",
            )
                .into_response()
        }
    };

    let callback = match lookup.handler {
        Some(cb) => cb,
        None if lookup.path_exists => {
            return (StatusCode::METHOD_NOT_ALLOWED, "method not allowed").into_response()
        }
        None => return (StatusCode::NOT_FOUND, "not found").into_response(),
    };

    let py_result = Python::with_gil(|py| -> PyResult<ResponsePayload> {
        let request = PyDict::new(py);
        request.set_item("method", &method)?;
        request.set_item("path", &path)?;
        request.set_item("query", query_to_dict(py, &query)?)?;
        request.set_item("headers", headers_to_dict(py, &headers)?)?;
        request.set_item("body", PyBytes::new(py, &body_bytes))?;
        request.set_item("text", String::from_utf8_lossy(&body_bytes).to_string())?;

        let py_obj = callback.call1(py, (request,))?;
        py_to_response(py, py_obj.as_ref(py), &state.template_dir)
    });

    match py_result {
        Ok(payload) => payload.into_response(),
        Err(err) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            format!("handler error: {err}"),
        )
            .into_response(),
    }
}

fn query_to_dict<'py>(py: Python<'py>, query: &str) -> PyResult<&'py PyDict> {
    let dict = PyDict::new(py);
    for (k, v) in form_urlencoded::parse(query.as_bytes()) {
        dict.set_item(k.as_ref(), v.as_ref())?;
    }
    Ok(dict)
}

fn headers_to_dict<'py>(
    py: Python<'py>,
    headers: &axum::http::HeaderMap<HeaderValue>,
) -> PyResult<&'py PyDict> {
    let dict = PyDict::new(py);
    for (name, value) in headers.iter() {
        let v = value.to_str().unwrap_or_default();
        dict.set_item(name.as_str(), v)?;
    }
    Ok(dict)
}

fn py_to_response(py: Python<'_>, obj: &PyAny, template_dir: &Path) -> PyResult<ResponsePayload> {
    if is_template_response(obj)? {
        let template: String = obj.getattr("template")?.extract()?;
        let context_json: String = obj.getattr("context_json")?.extract()?;
        let status: u16 = obj.getattr("status")?.extract()?;
        let headers = extract_headers(obj.getattr("headers")?)?;
        let html = render_template(template_dir, &template, &context_json)
            .map_err(PyRuntimeError::new_err)?;

        return Ok(ResponsePayload::new(
            html.into_bytes(),
            status,
            headers,
            Some("text/html; charset=utf-8".to_string()),
        ));
    }

    if let Ok(tuple) = obj.downcast::<PyTuple>() {
        return tuple_to_response(py, tuple);
    }

    let (body, content_type) = body_from_py(py, obj)?;
    Ok(ResponsePayload::new(body, 200, Vec::new(), content_type))
}

fn tuple_to_response(py: Python<'_>, tuple: &PyTuple) -> PyResult<ResponsePayload> {
    if tuple.len() != 2 && tuple.len() != 3 {
        return Err(PyValueError::new_err(
            "tuple response must be (body, status) or (body, status, headers)",
        ));
    }

    let body_obj = tuple.get_item(0)?;
    let status: u16 = tuple.get_item(1)?.extract()?;
    let headers = if tuple.len() == 3 {
        extract_headers(tuple.get_item(2)?)?
    } else {
        Vec::new()
    };

    let (body, content_type) = body_from_py(py, body_obj)?;
    Ok(ResponsePayload::new(body, status, headers, content_type))
}

fn body_from_py(py: Python<'_>, obj: &PyAny) -> PyResult<(Vec<u8>, Option<String>)> {
    if let Ok(text) = obj.extract::<String>() {
        return Ok((
            text.into_bytes(),
            Some("text/html; charset=utf-8".to_string()),
        ));
    }

    if let Ok(bytes) = obj.extract::<&[u8]>() {
        return Ok((bytes.to_vec(), Some("application/octet-stream".to_string())));
    }

    if obj.downcast::<PyDict>().is_ok() {
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

    let fallback = obj.str()?.to_string();
    Ok((
        fallback.into_bytes(),
        Some("text/plain; charset=utf-8".to_string()),
    ))
}

fn extract_headers(obj: &PyAny) -> PyResult<Vec<(String, String)>> {
    if obj.is_none() {
        return Ok(Vec::new());
    }

    let dict = obj
        .downcast::<PyDict>()
        .map_err(|_| PyValueError::new_err("headers must be a dict[str, str]"))?;

    let mut pairs = Vec::with_capacity(dict.len());
    for (k, v) in dict {
        pairs.push((k.str()?.to_string(), v.str()?.to_string()));
    }
    Ok(pairs)
}

fn is_template_response(obj: &PyAny) -> PyResult<bool> {
    if !obj.hasattr("__higuma_template__")? {
        return Ok(false);
    }
    obj.getattr("__higuma_template__")?.extract::<bool>()
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
}

impl IntoResponse for ResponsePayload {
    fn into_response(self) -> Response {
        let status = StatusCode::from_u16(self.status).unwrap_or(StatusCode::OK);
        let mut response = (status, self.body).into_response();

        if let Some(ct) = self.content_type {
            if let Ok(value) = HeaderValue::from_str(&ct) {
                response.headers_mut().insert(CONTENT_TYPE, value);
            }
        }

        for (name, value) in self.headers {
            if let (Ok(header_name), Ok(header_value)) = (
                HeaderName::from_bytes(name.as_bytes()),
                HeaderValue::from_str(&value),
            ) {
                response.headers_mut().insert(header_name, header_value);
            }
        }

        response
    }
}

#[pymodule]
fn _core(_py: Python<'_>, m: &PyModule) -> PyResult<()> {
    m.add_class::<HigumaCore>()?;
    Ok(())
}
