use axum::{
    body::Body,
    http::{StatusCode, header},
    response::Response,
};
use rust_embed::RustEmbed;

/// Embedded web UI assets from web/dist/ (shared build location)
#[derive(RustEmbed)]
#[folder = "../web/dist/"]
#[include = "*.html"]
#[include = "*.js"]
#[include = "*.css"]
#[include = "assets/*"]
pub struct Assets;

impl Assets {
    /// Serve an embedded asset with proper MIME type
    pub fn serve(path: &str) -> Response {
        // Normalize path (remove leading slash)
        let normalized = path.trim_start_matches('/');

        // Try to get asset
        match Assets::get(normalized) {
            Some(content) => {
                let mime_type = get_mime_type(normalized);

                Response::builder()
                    .status(StatusCode::OK)
                    .header(header::CONTENT_TYPE, mime_type)
                    .body(Body::from(content.data.into_owned()))
                    .unwrap_or_else(|e| {
                        tracing::error!("Failed to build asset response: {e}");
                        Self::internal_error_response()
                    })
            }
            None => {
                // Asset not found
                Response::builder()
                    .status(StatusCode::NOT_FOUND)
                    .body(Body::from("Asset not found"))
                    .unwrap_or_else(|e| {
                        tracing::error!("Failed to build 404 response: {e}");
                        Self::internal_error_response()
                    })
            }
        }
    }

    /// Create a fallback internal error response (cannot fail)
    fn internal_error_response() -> Response {
        Response::builder()
            .status(StatusCode::INTERNAL_SERVER_ERROR)
            .body(Body::from("Internal server error"))
            .expect("Failed to build fallback error response - this should never happen")
    }

    /// Serve index.html as SPA fallback
    pub fn serve_spa_fallback() -> Response {
        Self::serve("index.html")
    }
}

/// Get MIME type based on file extension
fn get_mime_type(path: &str) -> &'static str {
    if path.ends_with(".html") {
        "text/html; charset=utf-8"
    } else if path.ends_with(".js") {
        "application/javascript; charset=utf-8"
    } else if path.ends_with(".css") {
        "text/css; charset=utf-8"
    } else if path.ends_with(".json") {
        "application/json; charset=utf-8"
    } else if path.ends_with(".png") {
        "image/png"
    } else if path.ends_with(".jpg") || path.ends_with(".jpeg") {
        "image/jpeg"
    } else if path.ends_with(".svg") {
        "image/svg+xml"
    } else if path.ends_with(".ico") {
        "image/x-icon"
    } else if path.ends_with(".woff") {
        "font/woff"
    } else if path.ends_with(".woff2") {
        "font/woff2"
    } else {
        "application/octet-stream"
    }
}
