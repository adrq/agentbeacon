// SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
// SPDX-License-Identifier: AGPL-3.0-or-later

pub mod password;
pub mod principal;
pub mod setup;

use rand::RngCore;
use sha2::{Digest, Sha256};

/// A freshly minted token: the plaintext handed to its holder and the hash that is stored.
pub struct MintedToken {
    pub plaintext: String,
    pub hash: String,
}

/// Mint a 256-bit random token.
pub fn mint_token() -> MintedToken {
    let mut bytes = [0u8; 32];
    rand::rngs::OsRng.fill_bytes(&mut bytes);
    let plaintext = hex::encode(bytes);
    let hash = hash_token(&plaintext);
    MintedToken { plaintext, hash }
}

/// The stored form of a token: hex SHA-256.
pub fn hash_token(token: &str) -> String {
    hex::encode(Sha256::digest(token.as_bytes()))
}

/// Longest password accepted anywhere.
pub const PASSWORD_MAX_CHARS: usize = 256;
/// Shortest password accepted when one is set.
pub const PASSWORD_MIN_CHARS: usize = 8;
/// Longest username accepted.
pub const USERNAME_MAX_CHARS: usize = 64;

/// Check a username against the account rules.
pub fn validate_username(username: &str) -> Result<(), String> {
    let len = username.chars().count();
    if len == 0 || len > USERNAME_MAX_CHARS {
        return Err(format!(
            "username must be 1 to {USERNAME_MAX_CHARS} characters"
        ));
    }
    if !username
        .chars()
        .all(|c| c.is_ascii_alphanumeric() || c == '.' || c == '_' || c == '-')
    {
        return Err("username may contain only letters, digits, '.', '_' and '-'".to_string());
    }
    Ok(())
}

/// Check a password against the account rules.
pub fn validate_password(password: &str) -> Result<(), String> {
    let len = password.chars().count();
    if !(PASSWORD_MIN_CHARS..=PASSWORD_MAX_CHARS).contains(&len) {
        return Err(format!(
            "password must be {PASSWORD_MIN_CHARS} to {PASSWORD_MAX_CHARS} characters"
        ));
    }
    Ok(())
}
