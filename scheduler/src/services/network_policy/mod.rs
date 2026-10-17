// SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
// SPDX-License-Identifier: AGPL-3.0-or-later

//! Network entries and the execution's network ceiling.

pub mod ceiling;
pub mod entry;
pub mod sandbox_config;

pub use ceiling::{Ceiling, CeilingError, Mode, Policy};
pub use entry::{Entry, EntryError, check_authorable};
pub use sandbox_config::validate_sandbox_config;
