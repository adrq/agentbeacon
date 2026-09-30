// SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
// SPDX-License-Identifier: AGPL-3.0-or-later

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct SandboxPolicy {
    pub fs_level: FsLevel,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "snake_case")]
pub enum FsLevel {
    ReadOnly,
    Workspace,
    Unrestricted,
}

impl Default for SandboxPolicy {
    fn default() -> Self {
        Self {
            fs_level: FsLevel::Unrestricted,
        }
    }
}
