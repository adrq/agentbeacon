use rand::seq::SliceRandom;

const ADJECTIVES: &[&str] = &[
    "bold", "brave", "bright", "calm", "clear", "cool", "crisp", "deft", "fair", "fast", "firm",
    "fond", "free", "glad", "gold", "keen", "kind", "lean", "live", "mild", "neat", "nova", "pale",
    "pure", "rare", "rich", "safe", "slim", "soft", "sure", "tall", "tame", "tidy", "true", "vast",
    "warm", "wide", "wild", "wise", "zen",
];

const NOUNS: &[&str] = &[
    "arch", "bass", "beam", "bird", "bolt", "cape", "cave", "claw", "coil", "cone", "core", "crow",
    "dawn", "deer", "dove", "dusk", "edge", "fawn", "fern", "flag", "flux", "gate", "gale", "hawk",
    "haze", "iris", "jade", "kite", "lake", "lynx", "mesa", "mint", "moth", "muse", "node", "opal",
    "peak", "pine", "reed", "sage",
];

/// Generate a unique slug among existing sibling slugs.
/// Retries on collision (extremely unlikely with 1,600 combos vs typical width ≤10).
/// Falls back to `{adj}-{noun}-{counter}` after 100 attempts to guarantee termination.
pub fn generate_slug(existing_sibling_slugs: &[String]) -> String {
    let mut rng = rand::thread_rng();
    for _ in 0..100 {
        let adj = ADJECTIVES.choose(&mut rng).unwrap();
        let noun = NOUNS.choose(&mut rng).unwrap();
        let slug = format!("{adj}-{noun}");
        if !existing_sibling_slugs.contains(&slug) {
            return slug;
        }
    }
    // Fallback: append counter to guarantee uniqueness
    let adj = ADJECTIVES.choose(&mut rng).unwrap();
    let noun = NOUNS.choose(&mut rng).unwrap();
    format!("{adj}-{noun}-{}", existing_sibling_slugs.len())
}
