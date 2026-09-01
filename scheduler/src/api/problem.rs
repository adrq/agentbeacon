use axum::{
    http::{StatusCode, header},
    response::{IntoResponse, Response},
};
use serde_json::{Map, Value};

/// Media type for RFC 9457 problem responses.
pub const PROBLEM_CONTENT_TYPE: &str = "application/problem+json";

/// Machine-readable error identifiers returned in the `code` member.
///
/// A code is never renamed and never reused for a different condition.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ProblemCode {
    RequestInvalid,
    ResourceNotFound,
    ResourceConflict,
    AuthUnauthorized,
    AuthForbidden,
    PayloadTooLarge,
    MethodNotAllowed,
    InternalError,
    WikiRevisionConflict,
    WikiSlugConflict,
    WikiConfirmationRequired,
    DecisionAlreadyResolved,
    DecisionExpired,
    DecisionNotFound,
    DecisionWrongSession,
    DecisionAnswerTooLarge,
    ExecutionTerminated,
}

impl ProblemCode {
    /// The value serialized in the `code` member.
    pub const fn as_str(&self) -> &'static str {
        match self {
            ProblemCode::RequestInvalid => "request.invalid",
            ProblemCode::ResourceNotFound => "resource.not_found",
            ProblemCode::ResourceConflict => "resource.conflict",
            ProblemCode::AuthUnauthorized => "auth.unauthorized",
            ProblemCode::AuthForbidden => "auth.forbidden",
            ProblemCode::PayloadTooLarge => "request.too_large",
            ProblemCode::MethodNotAllowed => "request.method_not_allowed",
            ProblemCode::InternalError => "internal.error",
            ProblemCode::WikiRevisionConflict => "wiki.revision_conflict",
            ProblemCode::WikiSlugConflict => "wiki.slug_conflict",
            ProblemCode::WikiConfirmationRequired => "wiki.confirmation_required",
            ProblemCode::DecisionAlreadyResolved => "decision.already_resolved",
            ProblemCode::DecisionExpired => "decision.expired",
            ProblemCode::DecisionNotFound => "decision.not_found",
            ProblemCode::DecisionWrongSession => "decision.wrong_session",
            ProblemCode::DecisionAnswerTooLarge => "decision.answer_too_large",
            ProblemCode::ExecutionTerminated => "execution.terminated",
        }
    }

    /// The HTTP status the code is always returned with.
    pub const fn status(&self) -> StatusCode {
        match self {
            ProblemCode::RequestInvalid => StatusCode::BAD_REQUEST,
            ProblemCode::ResourceNotFound => StatusCode::NOT_FOUND,
            ProblemCode::ResourceConflict => StatusCode::CONFLICT,
            ProblemCode::AuthUnauthorized => StatusCode::UNAUTHORIZED,
            ProblemCode::AuthForbidden => StatusCode::FORBIDDEN,
            ProblemCode::PayloadTooLarge => StatusCode::PAYLOAD_TOO_LARGE,
            ProblemCode::MethodNotAllowed => StatusCode::METHOD_NOT_ALLOWED,
            ProblemCode::InternalError => StatusCode::INTERNAL_SERVER_ERROR,
            ProblemCode::WikiRevisionConflict => StatusCode::CONFLICT,
            ProblemCode::WikiSlugConflict => StatusCode::CONFLICT,
            ProblemCode::WikiConfirmationRequired => StatusCode::CONFLICT,
            ProblemCode::DecisionAlreadyResolved => StatusCode::CONFLICT,
            ProblemCode::DecisionExpired => StatusCode::CONFLICT,
            ProblemCode::DecisionNotFound => StatusCode::NOT_FOUND,
            ProblemCode::DecisionWrongSession => StatusCode::CONFLICT,
            ProblemCode::DecisionAnswerTooLarge => StatusCode::BAD_REQUEST,
            ProblemCode::ExecutionTerminated => StatusCode::CONFLICT,
        }
    }

    /// Short human-readable summary serialized in the `title` member.
    pub const fn title(&self) -> &'static str {
        match self {
            ProblemCode::RequestInvalid => "Invalid request",
            ProblemCode::ResourceNotFound => "Resource not found",
            ProblemCode::ResourceConflict => "Conflict",
            ProblemCode::AuthUnauthorized => "Unauthorized",
            ProblemCode::AuthForbidden => "Forbidden",
            ProblemCode::PayloadTooLarge => "Request too large",
            ProblemCode::MethodNotAllowed => "Method not allowed",
            ProblemCode::InternalError => "Internal error",
            ProblemCode::WikiRevisionConflict => "Revision conflict",
            ProblemCode::WikiSlugConflict => "Slug already exists",
            ProblemCode::WikiConfirmationRequired => "Confirmation required",
            ProblemCode::DecisionAlreadyResolved => "Decision already resolved",
            ProblemCode::DecisionExpired => "Decision expired",
            ProblemCode::DecisionNotFound => "Decision not found",
            ProblemCode::DecisionWrongSession => "Decision belongs to a different session",
            ProblemCode::DecisionAnswerTooLarge => "Answer too large",
            ProblemCode::ExecutionTerminated => "Execution terminated",
        }
    }
}

/// An RFC 9457 problem response.
///
/// Serializes `title`, `status`, `code`, an optional `detail`, and any
/// extension members added by the caller.
#[derive(Debug, Clone)]
pub struct Problem {
    code: ProblemCode,
    detail: Option<String>,
    extensions: Map<String, Value>,
}

impl Problem {
    /// Build a problem for `code` with no detail and no extensions.
    pub fn new(code: ProblemCode) -> Self {
        Self {
            code,
            detail: None,
            extensions: Map::new(),
        }
    }

    /// Attach display text describing what the caller did.
    pub fn with_detail(mut self, detail: impl Into<String>) -> Self {
        self.detail = Some(detail.into());
        self
    }

    /// Attach an extension member.
    pub fn with_extension(mut self, key: impl Into<String>, value: impl Into<Value>) -> Self {
        self.extensions.insert(key.into(), value.into());
        self
    }

    /// The code this problem reports.
    pub fn code(&self) -> ProblemCode {
        self.code
    }

    /// The HTTP status this problem is returned with.
    pub fn status(&self) -> StatusCode {
        self.code.status()
    }

    /// The serialized body.
    pub fn to_value(&self) -> Value {
        let mut body = Map::new();
        body.insert("title".into(), Value::String(self.code.title().into()));
        body.insert("status".into(), Value::from(self.code.status().as_u16()));
        if let Some(detail) = &self.detail {
            body.insert("detail".into(), Value::String(detail.clone()));
        }
        body.insert("code".into(), Value::String(self.code.as_str().into()));
        for (key, value) in &self.extensions {
            body.insert(key.clone(), value.clone());
        }
        Value::Object(body)
    }
}

impl IntoResponse for Problem {
    fn into_response(self) -> Response {
        let body = serde_json::to_vec(&self.to_value()).unwrap_or_else(|_| b"{}".to_vec());
        (
            self.code.status(),
            [(header::CONTENT_TYPE, PROBLEM_CONTENT_TYPE)],
            body,
        )
            .into_response()
    }
}

/// Convenience constructor for a `resource.not_found` problem.
pub fn not_found(detail: impl Into<String>) -> Problem {
    Problem::new(ProblemCode::ResourceNotFound).with_detail(detail)
}

/// Convenience constructor for a `request.invalid` problem.
pub fn invalid(detail: impl Into<String>) -> Problem {
    Problem::new(ProblemCode::RequestInvalid).with_detail(detail)
}
