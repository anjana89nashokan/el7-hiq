import time
import logging
from google import genai
from config.settings import config
from threading import Lock
from typing import Optional, Union, List
from google.genai import types

logger = logging.getLogger(__name__)

class RateLimiter:
    """
    Enhanced rate limiter with automatic token counting and adaptive waiting.
    Prevents 429 Resource Exhausted errors by tracking both RPM and TPM limits.
    """
    _instance = None
    _lock = Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(RateLimiter, cls).__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self):
        if self._initialized:
            return
            
        self._initialized = True
        self.rpm_limit = config.LLM_RPM_LIMIT
        self.tpm_limit = config.LLM_TPM_LIMIT
        
        # Buckets for tracking usage
        self.request_timestamps = []
        self.token_timestamps = []  # List of (timestamp, token_count)
        
        # Safety margins (use 90% of limits to be safe)
        self.rpm_safety_margin = 0.9
        self.tpm_safety_margin = 0.9
        
        # Client for counting tokens
        try:
            self.client = genai.Client(
                vertexai=True, 
                location=config.GOOGLE_CLOUD_LOCATION, 
                project=config.GOOGLE_CLOUD_PROJECT
            )
            self.model_name = config.AGENT_MODEL
            logger.info(f"EnhancedRateLimiter initialized with RPM={self.rpm_limit}, TPM={self.tpm_limit}")
        except Exception as e:
            logger.error(f"Failed to initialize EnhancedRateLimiter GenAI client: {e}")
            self.client = None

    def _cleanup_timestamps(self, window_seconds=60):
        """Remove timestamps older than the window."""
        now = time.time()
        cutoff = now - window_seconds
        self.request_timestamps = [t for t in self.request_timestamps if t > cutoff]
        self.token_timestamps = [(t, c) for t, c in self.token_timestamps if t > cutoff]

    def count_tokens(self, text: str) -> int:
        """Count tokens for the given text using the GenAI client."""
        if not text:
            return 0
            
        if not self.client:
            # Fallback estimation: roughly 4 chars per token
            return max(1, len(str(text)) // 4)
            
        try:
            response = self.client.models.count_tokens(
                model=self.model_name, 
                contents=text
            )
            token_count = response.total_tokens
            logger.debug(f"Token count: {token_count} for text length: {len(text)}")
            return token_count
        except Exception as e:
            logger.warning(f"Failed to count tokens via API, using fallback: {e}")
            return max(1, len(str(text)) // 4)

    def count_tokens_for_message(self, message: Union[str, types.Content, dict]) -> int:
        """
        Count tokens for various message formats.
        Handles string, Content objects, and dictionaries.
        """
        try:
            if isinstance(message, str):
                return self.count_tokens(message)
            
            elif isinstance(message, types.Content):
                # Extract text from Content parts
                full_text = ""
                for part in message.parts:
                    if hasattr(part, 'text') and part.text:
                        full_text += part.text
                return self.count_tokens(full_text)
            
            elif isinstance(message, dict):
                # Handle dictionary format
                if 'text' in message:
                    return self.count_tokens(message['text'])
                elif 'parts' in message:
                    full_text = ""
                    for part in message['parts']:
                        if isinstance(part, dict) and 'text' in part:
                            full_text += part['text']
                        elif hasattr(part, 'text'):
                            full_text += part.text
                    return self.count_tokens(full_text)
            
            # Fallback: convert to string
            return self.count_tokens(str(message))
            
        except Exception as e:
            logger.warning(f"Error counting tokens for message: {e}")
            return 100  # Conservative estimate

    def estimate_response_tokens(self, request_tokens: int) -> int:
        """
        Estimate response tokens based on request.
        Conservative estimate: assume response is similar size to request.
        """
        # Use a multiplier for safety (response might be longer)
        return int(request_tokens * 1.5)

    def calculate_total_tokens(self, message: Union[str, types.Content, dict]) -> int:
        """
        Calculate total tokens including estimated response.
        This is what we'll reserve in the rate limiter.
        """
        request_tokens = self.count_tokens_for_message(message)
        response_tokens = self.estimate_response_tokens(request_tokens)
        total_tokens = request_tokens + response_tokens
        
        logger.debug(
            f"Token calculation: request={request_tokens}, "
            f"estimated_response={response_tokens}, total={total_tokens}"
        )
        
        return total_tokens

    def get_current_usage(self) -> dict:
        """Get current rate limit usage statistics."""
        self._cleanup_timestamps()
        
        current_rpm = len(self.request_timestamps)
        current_tpm = sum(c for _, c in self.token_timestamps)
        
        return {
            'current_rpm': current_rpm,
            'rpm_limit': self.rpm_limit,
            'rpm_percentage': (current_rpm / self.rpm_limit * 100) if self.rpm_limit > 0 else 0,
            'current_tpm': current_tpm,
            'tpm_limit': self.tpm_limit,
            'tpm_percentage': (current_tpm / self.tpm_limit * 100) if self.tpm_limit > 0 else 0,
        }

    def calculate_wait_time(self, estimated_tokens: int) -> float:
        """
        Calculate how long to wait before the next request can proceed.
        Returns wait time in seconds.
        """
        self._cleanup_timestamps()
        
        current_rpm = len(self.request_timestamps)
        current_tpm = sum(c for _, c in self.token_timestamps)
        
        wait_time = 0.0
        
        # Check RPM limit
        effective_rpm_limit = int(self.rpm_limit * self.rpm_safety_margin)
        if current_rpm >= effective_rpm_limit:
            # Find oldest request timestamp
            if self.request_timestamps:
                oldest_request = min(self.request_timestamps)
                time_until_oldest_expires = 60 - (time.time() - oldest_request)
                wait_time = max(wait_time, time_until_oldest_expires)
        
        # Check TPM limit
        effective_tpm_limit = int(self.tpm_limit * self.tpm_safety_margin)
        if (current_tpm + estimated_tokens) > effective_tpm_limit:
            # Find when enough tokens will expire
            sorted_tokens = sorted(self.token_timestamps, key=lambda x: x[0])
            accumulated_tokens = current_tpm
            
            for timestamp, token_count in sorted_tokens:
                accumulated_tokens -= token_count
                if (accumulated_tokens + estimated_tokens) <= effective_tpm_limit:
                    time_until_expires = 60 - (time.time() - timestamp)
                    wait_time = max(wait_time, time_until_expires)
                    break
        
        return max(0, wait_time)

    def wait_for_availability(self, estimated_tokens: int = 0):
        """
        Check if we are within limits. If not, sleep until we are.
        Updates the usage tracking after waiting.
        """
        if estimated_tokens <= 0:
            estimated_tokens = 100  # Minimum estimate
        
        with self._lock:
            iteration = 0
            while True:
                iteration += 1
                self._cleanup_timestamps()
                
                current_rpm = len(self.request_timestamps)
                current_tpm = sum(c for _, c in self.token_timestamps)
                
                effective_rpm_limit = int(self.rpm_limit * self.rpm_safety_margin)
                effective_tpm_limit = int(self.tpm_limit * self.tpm_safety_margin)
                
                rpm_ok = current_rpm < effective_rpm_limit
                tpm_ok = (current_tpm + estimated_tokens) <= effective_tpm_limit
                
                if rpm_ok and tpm_ok:
                    # Record usage
                    now = time.time()
                    self.request_timestamps.append(now)
                    self.token_timestamps.append((now, estimated_tokens))
                    
                    # Log warnings if approaching limits
                    if current_rpm > 0.8 * effective_rpm_limit:
                        logger.warning(
                            f"Approaching RPM limit: {current_rpm}/{effective_rpm_limit} "
                            f"({current_rpm/effective_rpm_limit*100:.1f}%)"
                        )
                    if current_tpm > 0.8 * effective_tpm_limit:
                        logger.warning(
                            f"Approaching TPM limit: {current_tpm}/{effective_tpm_limit} "
                            f"({current_tpm/effective_tpm_limit*100:.1f}%)"
                        )
                    
                    return
                
                # Calculate smart wait time
                wait_time = self.calculate_wait_time(estimated_tokens)
                
                # Add small buffer
                wait_time = max(2.0, wait_time + 0.5)
                
                logger.info(
                    f"Rate limit reached (iteration {iteration}): "
                    f"RPM={current_rpm}/{effective_rpm_limit}, "
                    f"TPM={current_tpm + estimated_tokens}/{effective_tpm_limit}. "
                    f"Waiting {wait_time:.1f}s..."
                )
                
                time.sleep(wait_time)

    def safe_wait_before_request(self, message: Union[str, types.Content, dict]):
        """
        Convenience method: calculates tokens and waits if needed.
        Use this before making API calls in loops.
        """
        total_tokens = self.calculate_total_tokens(message)
        self.wait_for_availability(total_tokens)
        logger.debug(f"Proceeding with request (reserved {total_tokens} tokens)")


# Singleton instance
rate_limiter = RateLimiter()