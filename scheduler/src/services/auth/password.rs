// SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
// SPDX-License-Identifier: AGPL-3.0-or-later

use argon2::Argon2;
use argon2::password_hash::{
    PasswordHash, PasswordHasher, PasswordVerifier, SaltString, rand_core::OsRng,
};
use std::sync::OnceLock;

/// Hash a password with Argon2id into a PHC string.
pub fn hash_password(password: &str) -> Result<String, String> {
    let salt = SaltString::generate(&mut OsRng);
    Argon2::default()
        .hash_password(password.as_bytes(), &salt)
        .map(|h| h.to_string())
        .map_err(|e| format!("hash password failed: {e}"))
}

/// Verify a password against a stored PHC string.
pub fn verify_password(password: &str, stored: &str) -> bool {
    match PasswordHash::new(stored) {
        Ok(parsed) => Argon2::default()
            .verify_password(password.as_bytes(), &parsed)
            .is_ok(),
        Err(_) => false,
    }
}

/// Run one verification against a fixed hash and discard the result.
pub fn verify_against_placeholder(password: &str) {
    static PLACEHOLDER: OnceLock<String> = OnceLock::new();
    let stored =
        PLACEHOLDER.get_or_init(|| hash_password("placeholder-password").unwrap_or_default());
    let _ = verify_password(password, stored);
}
