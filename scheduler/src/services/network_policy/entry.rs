// SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
// SPDX-License-Identifier: AGPL-3.0-or-later

//! Network entries: `host:ports` in canonical form.

use std::fmt;
use std::net::{IpAddr, Ipv4Addr, Ipv6Addr};

use idna::uts46::{AsciiDenyList, DnsLength, Hyphens, Uts46};
use ipnet::{IpNet, Ipv6Net};

/// Host part of an entry.
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub enum HostPart {
    /// A DNS name (lowercase A-labels).
    Name(String),
    /// `*.suffix`: every name strictly below `suffix`.
    Wildcard(String),
    Addr(IpAddr),
    /// A CIDR prefix with no host bits set, shorter than a single address.
    Net(IpNet),
}

impl HostPart {
    fn from_net(net: IpNet) -> HostPart {
        if net.prefix_len() == net.max_prefix_len() {
            HostPart::Addr(net.addr())
        } else {
            HostPart::Net(net)
        }
    }

    fn net(&self) -> Option<IpNet> {
        match self {
            HostPart::Addr(a) => Some(IpNet::from(*a)),
            HostPart::Net(n) => Some(*n),
            _ => None,
        }
    }
}

impl fmt::Display for HostPart {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            HostPart::Name(n) => f.write_str(n),
            HostPart::Wildcard(s) => write!(f, "*.{s}"),
            HostPart::Addr(IpAddr::V4(a)) => write!(f, "{a}"),
            HostPart::Addr(IpAddr::V6(a)) => write!(f, "[{a}]"),
            HostPart::Net(IpNet::V4(n)) => write!(f, "{n}"),
            HostPart::Net(IpNet::V6(n)) => write!(f, "[{n}]"),
        }
    }
}

/// Inclusive port interval; `*` is `1-65535`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct PortRange {
    lo: u16,
    hi: u16,
}

impl PortRange {
    pub const ALL: PortRange = PortRange { lo: 1, hi: 65535 };

    pub fn new(lo: u16, hi: u16) -> Option<PortRange> {
        (lo >= 1 && lo <= hi).then_some(PortRange { lo, hi })
    }
}

impl fmt::Display for PortRange {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        if *self == PortRange::ALL {
            f.write_str("*")
        } else if self.lo == self.hi {
            write!(f, "{}", self.lo)
        } else {
            write!(f, "{}-{}", self.lo, self.hi)
        }
    }
}

/// One network entry. `Display` is the canonical wire string.
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct Entry {
    host: HostPart,
    ports: PortRange,
}

/// A rejected entry, spelled as the caller wrote it, and the reason.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct EntryError {
    pub entry: String,
    pub reason: String,
}

impl fmt::Display for EntryError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{:?}: {}", self.entry, self.reason)
    }
}

impl Entry {
    /// Parse an entry, canonicalizing equivalent spellings and rejecting
    /// everything else.
    pub fn parse(raw: &str) -> Result<Entry, EntryError> {
        let fail = |reason: String| EntryError {
            entry: raw.to_string(),
            reason,
        };
        let (host_raw, ports_raw) = split_host_ports(raw).map_err(fail)?;
        let ports = parse_ports(ports_raw).map_err(fail)?;
        let host = parse_host(host_raw).map_err(fail)?;
        Ok(Entry { host, ports })
    }
}

impl fmt::Display for Entry {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}:{}", self.host, self.ports)
    }
}

/// Check an entry a human authored: a literal wholly inside guest loopback
/// (`127.0.0.0/8`, `[::1]`) is refused. Returns the reason on refusal.
pub fn check_authorable(entry: &Entry) -> Result<(), &'static str> {
    match entry.host.net() {
        Some(net) if is_guest_loopback(&net) => Err(
            "loopback addresses refer to the sandbox itself; use host.internal to reach the host",
        ),
        _ => Ok(()),
    }
}

fn split_host_ports(raw: &str) -> Result<(&str, &str), String> {
    if let Some(rest) = raw.strip_prefix('[') {
        let close = rest
            .find(']')
            .ok_or_else(|| "missing closing bracket".to_string())?;
        let ports = rest[close + 1..]
            .strip_prefix(':')
            .ok_or_else(|| "missing port; use host:port, host:lo-hi or host:*".to_string())?;
        return Ok((&raw[..close + 2], ports));
    }
    let (host, ports) = raw
        .rsplit_once(':')
        .ok_or_else(|| "missing port; use host:port, host:lo-hi or host:*".to_string())?;
    if host.contains(':') {
        return Err("IPv6 addresses must be written in brackets, as [addr]:port".to_string());
    }
    if host.is_empty() {
        return Err("missing host".to_string());
    }
    Ok((host, ports))
}

fn parse_port(s: &str) -> Result<u16, String> {
    if s.is_empty() || !s.bytes().all(|b| b.is_ascii_digit()) {
        return Err(format!("invalid port {s:?}"));
    }
    if s.len() > 1 && s.starts_with('0') {
        return Err(format!("port {s:?} has a leading zero"));
    }
    match s.parse::<u32>() {
        Ok(p) if (1..=65535).contains(&p) => Ok(p as u16),
        _ => Err(format!("port {s} is outside 1-65535")),
    }
}

fn parse_ports(s: &str) -> Result<PortRange, String> {
    if s == "*" {
        return Ok(PortRange::ALL);
    }
    let (lo, hi) = match s.split_once('-') {
        Some((lo, hi)) => (parse_port(lo)?, parse_port(hi)?),
        None => {
            let p = parse_port(s)?;
            (p, p)
        }
    };
    PortRange::new(lo, hi).ok_or_else(|| format!("port range {s} ends before it starts"))
}

fn parse_host(s: &str) -> Result<HostPart, String> {
    if let Some(inner) = s.strip_prefix('[').and_then(|r| r.strip_suffix(']')) {
        return parse_ipv6(inner);
    }
    if !s.is_empty()
        && s.bytes()
            .all(|b| b.is_ascii_digit() || b == b'.' || b == b'/')
    {
        return parse_ipv4(s);
    }
    if let Some(suffix) = s.strip_prefix("*.") {
        if suffix.contains('*') {
            return Err("a wildcard is exactly one leading \"*.\"".to_string());
        }
        if parse_host(suffix).is_ok_and(|h| h.net().is_some()) {
            return Err("a wildcard cannot apply to an address".to_string());
        }
        let name = parse_name(suffix, 2)?;
        return Ok(HostPart::Wildcard(name));
    }
    if s.contains('*') {
        return Err("a wildcard is exactly one leading \"*.\"".to_string());
    }
    parse_name(s, 0).map(HostPart::Name)
}

/// `extra` counts octets outside the name itself (the `*.` of a wildcard)
/// toward the 253-octet limit.
fn parse_name(s: &str, extra: usize) -> Result<String, String> {
    let mapped = Uts46::new()
        .to_ascii(
            s.as_bytes(),
            AsciiDenyList::STD3,
            Hyphens::Check,
            DnsLength::VerifyAllowRootDot,
        )
        .map_err(|_| {
            "invalid host name: labels are letters, digits and inner hyphens (no \"_\", no \"--\" in positions 3-4), at most 63 octets each and 253 in total".to_string()
        })?;
    let ascii = mapped.strip_suffix('.').unwrap_or(&mapped);
    if ascii.len() + extra > 253 {
        return Err("host name is longer than 253 octets".to_string());
    }
    let last = ascii.rsplit('.').next().unwrap_or_default();
    if last.bytes().all(|b| b.is_ascii_digit()) {
        return Err("the last label of a host name cannot be all digits".to_string());
    }
    Ok(ascii.to_string())
}

fn parse_ipv4(s: &str) -> Result<HostPart, String> {
    let (addr_s, prefix) = split_prefix(s)?;
    let octets: Vec<&str> = addr_s.split('.').collect();
    if octets.len() != 4 {
        return Err("invalid IPv4 address".to_string());
    }
    let mut bytes = [0u8; 4];
    for (i, o) in octets.iter().enumerate() {
        if o.is_empty() || o.len() > 3 {
            return Err("invalid IPv4 address".to_string());
        }
        if o.len() > 1 && o.starts_with('0') {
            return Err("IPv4 octets cannot have leading zeros".to_string());
        }
        bytes[i] = o
            .parse::<u8>()
            .map_err(|_| "invalid IPv4 address".to_string())?;
    }
    let net = make_net(IpAddr::V4(Ipv4Addr::from(bytes)), prefix, 32)?;
    Ok(HostPart::from_net(net))
}

fn parse_ipv6(s: &str) -> Result<HostPart, String> {
    if s.contains('%') {
        return Err("IPv6 zone identifiers are not allowed".to_string());
    }
    let (addr_s, prefix) = split_prefix(s)?;
    let addr: Ipv6Addr = addr_s
        .parse()
        .map_err(|_| "invalid IPv6 address".to_string())?;
    let net = make_net(IpAddr::V6(addr), prefix, 128)?;
    let mapped = Ipv6Net::new(Ipv6Addr::new(0, 0, 0, 0, 0, 0xffff, 0, 0), 96).expect("valid");
    if let IpNet::V6(n) = net
        && mapped.contains(&n)
    {
        return Err(
            "IPv4-mapped IPv6 addresses are not allowed; write the IPv4 address".to_string(),
        );
    }
    Ok(HostPart::from_net(net))
}

fn split_prefix(s: &str) -> Result<(&str, Option<u8>), String> {
    match s.split_once('/') {
        None => Ok((s, None)),
        Some((a, p)) => {
            if p.is_empty()
                || !p.bytes().all(|b| b.is_ascii_digit())
                || (p.len() > 1 && p.starts_with('0'))
            {
                return Err(format!("invalid prefix length {p:?}"));
            }
            let len = p
                .parse::<u8>()
                .map_err(|_| format!("invalid prefix length {p:?}"))?;
            Ok((a, Some(len)))
        }
    }
}

fn make_net(addr: IpAddr, prefix: Option<u8>, max: u8) -> Result<IpNet, String> {
    let len = prefix.unwrap_or(max);
    let net = IpNet::new(addr, len).map_err(|_| format!("prefix length {len} is too long"))?;
    let network = net.trunc();
    if network != net {
        return Err(format!(
            "prefix has host bits set; the network is {}",
            HostPart::from_net(network)
        ));
    }
    Ok(net)
}

/// Whether a prefix lies wholly inside guest loopback.
fn is_guest_loopback(net: &IpNet) -> bool {
    match net {
        IpNet::V4(n) => n.prefix_len() >= 8 && n.addr().octets()[0] == 127,
        IpNet::V6(n) => n.prefix_len() == 128 && n.addr() == Ipv6Addr::LOCALHOST,
    }
}
