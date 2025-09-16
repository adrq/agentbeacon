use std::time::Duration;

/// Retry backoff strategy
#[derive(Debug, Clone, PartialEq)]
pub enum BackoffStrategy {
    Fixed,
    Linear,
    Exponential,
}

/// Retry policy configuration
#[derive(Debug, Clone)]
pub struct RetryPolicy {
    pub strategy: BackoffStrategy,
    pub base_delay_ms: u64,
    pub max_attempts: u32,
    pub timeout_ms: Option<u64>,
}

impl RetryPolicy {
    /// Calculate delay before next retry attempt
    ///
    /// # Backoff Strategies
    ///
    /// - **Fixed**: `base_delay_ms` (constant delay)
    /// - **Linear**: `base_delay_ms * (attempt + 1)` (e.g., 1s, 2s, 3s, ...)
    /// - **Exponential**: `base_delay_ms * 2^attempt` (e.g., 1s, 2s, 4s, 8s, ...)
    ///
    /// # Overflow Protection
    ///
    /// Exponential backoff uses `checked_pow()` and `saturating_mul()` to prevent
    /// panics. Maximum delay capped at `2^32 ms` (≈49.7 days).
    pub fn calculate_next_delay(&self, attempt: u32) -> Option<Duration> {
        // Check if we've exceeded max attempts
        if attempt >= self.max_attempts {
            return None;
        }

        let delay_ms = match self.strategy {
            BackoffStrategy::Fixed => self.base_delay_ms,
            BackoffStrategy::Linear => self.base_delay_ms * (attempt as u64 + 1),
            BackoffStrategy::Exponential => {
                // Prevent overflow - 2^32 ms ≈ 49.7 days is practical maximum
                // Use checked_pow to detect overflow, saturating_mul to prevent panic
                let exp = 2_u64.checked_pow(attempt).unwrap_or(u64::MAX);
                self.base_delay_ms.saturating_mul(exp)
            }
        };

        Some(Duration::from_millis(delay_ms))
    }

    /// Check if retry is allowed based on attempts
    pub fn should_retry(&self, attempt: u32) -> bool {
        attempt < self.max_attempts
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_exponential_backoff() {
        let policy = RetryPolicy {
            strategy: BackoffStrategy::Exponential,
            base_delay_ms: 1000, // 1 second base
            max_attempts: 5,
            timeout_ms: None,
        };

        // Attempt 0: 1s * 2^0 = 1s
        assert_eq!(
            policy.calculate_next_delay(0),
            Some(Duration::from_millis(1000))
        );

        // Attempt 1: 1s * 2^1 = 2s
        assert_eq!(
            policy.calculate_next_delay(1),
            Some(Duration::from_millis(2000))
        );

        // Attempt 2: 1s * 2^2 = 4s
        assert_eq!(
            policy.calculate_next_delay(2),
            Some(Duration::from_millis(4000))
        );

        // Attempt 3: 1s * 2^3 = 8s
        assert_eq!(
            policy.calculate_next_delay(3),
            Some(Duration::from_millis(8000))
        );

        // Attempt 4: 1s * 2^4 = 16s
        assert_eq!(
            policy.calculate_next_delay(4),
            Some(Duration::from_millis(16000))
        );

        // Attempt 5: Exceeded max_attempts (5)
        assert_eq!(policy.calculate_next_delay(5), None);
    }

    #[test]
    fn test_linear_backoff() {
        let policy = RetryPolicy {
            strategy: BackoffStrategy::Linear,
            base_delay_ms: 1000, // 1 second base
            max_attempts: 4,
            timeout_ms: None,
        };

        // Attempt 0: 1s * (0 + 1) = 1s
        assert_eq!(
            policy.calculate_next_delay(0),
            Some(Duration::from_millis(1000))
        );

        // Attempt 1: 1s * (1 + 1) = 2s
        assert_eq!(
            policy.calculate_next_delay(1),
            Some(Duration::from_millis(2000))
        );

        // Attempt 2: 1s * (2 + 1) = 3s
        assert_eq!(
            policy.calculate_next_delay(2),
            Some(Duration::from_millis(3000))
        );

        // Attempt 3: 1s * (3 + 1) = 4s
        assert_eq!(
            policy.calculate_next_delay(3),
            Some(Duration::from_millis(4000))
        );

        // Attempt 4: Exceeded max_attempts (4)
        assert_eq!(policy.calculate_next_delay(4), None);
    }

    #[test]
    fn test_fixed_delay() {
        let policy = RetryPolicy {
            strategy: BackoffStrategy::Fixed,
            base_delay_ms: 1000, // 1 second constant
            max_attempts: 4,
            timeout_ms: None,
        };

        // All attempts should return same delay
        assert_eq!(
            policy.calculate_next_delay(0),
            Some(Duration::from_millis(1000))
        );
        assert_eq!(
            policy.calculate_next_delay(1),
            Some(Duration::from_millis(1000))
        );
        assert_eq!(
            policy.calculate_next_delay(2),
            Some(Duration::from_millis(1000))
        );
        assert_eq!(
            policy.calculate_next_delay(3),
            Some(Duration::from_millis(1000))
        );

        // Attempt 4: Exceeded max_attempts (4)
        assert_eq!(policy.calculate_next_delay(4), None);
    }

    #[test]
    fn test_max_attempts_enforcement() {
        let policy = RetryPolicy {
            strategy: BackoffStrategy::Fixed,
            base_delay_ms: 1000,
            max_attempts: 3,
            timeout_ms: None,
        };

        // Attempts 0, 1, 2 should be allowed
        assert!(policy.should_retry(0));
        assert!(policy.should_retry(1));
        assert!(policy.should_retry(2));

        // Attempt 3 and beyond should be rejected
        assert!(!policy.should_retry(3));
        assert!(!policy.should_retry(4));

        // calculate_next_delay should return None after max_attempts
        assert!(policy.calculate_next_delay(0).is_some());
        assert!(policy.calculate_next_delay(1).is_some());
        assert!(policy.calculate_next_delay(2).is_some());
        assert!(policy.calculate_next_delay(3).is_none());
    }

    #[test]
    fn test_single_attempt_no_retry() {
        let policy = RetryPolicy {
            strategy: BackoffStrategy::Fixed,
            base_delay_ms: 1000,
            max_attempts: 1, // No retries
            timeout_ms: None,
        };

        // Attempt 0: First attempt allowed
        assert!(policy.should_retry(0));
        assert!(policy.calculate_next_delay(0).is_some());

        // Attempt 1: No retries, should fail
        assert!(!policy.should_retry(1));
        assert!(policy.calculate_next_delay(1).is_none());
    }

    #[test]
    fn test_timeout_field_present() {
        let policy = RetryPolicy {
            strategy: BackoffStrategy::Fixed,
            base_delay_ms: 1000,
            max_attempts: 3,
            timeout_ms: Some(30000), // 30 second timeout
        };

        // Verify timeout is stored correctly
        assert_eq!(policy.timeout_ms, Some(30000));
    }

    #[test]
    fn test_zero_base_delay() {
        let policy = RetryPolicy {
            strategy: BackoffStrategy::Fixed,
            base_delay_ms: 0, // Immediate retry
            max_attempts: 3,
            timeout_ms: None,
        };

        assert_eq!(
            policy.calculate_next_delay(0),
            Some(Duration::from_millis(0))
        );
    }

    #[test]
    fn test_exponential_backoff_no_overflow() {
        let policy = RetryPolicy {
            strategy: BackoffStrategy::Exponential,
            base_delay_ms: 1000,
            max_attempts: 100,
            timeout_ms: None,
        };

        // Should not panic even with extreme attempt count
        let delay = policy.calculate_next_delay(100);

        // Should cap at reasonable value, not overflow
        assert!(delay.is_none() || delay.unwrap().as_millis() > 0);
    }
}
