# triager.py
import time
import anthropic

class BudgetExceededError(Exception):
    """Custom exception raised when an instance exhausts its allocated budget."""
    pass

class TicketTriager:
    def __init__(self, model_key: str, budget_usd: float):
        """
        Manages ticket classification using custom pricing matrices.
        Tracks spend and call counts independently per instance.
        """
        self.model_key = model_key
        self.budget_usd = budget_usd
        self.spent_usd = 0.0
        self.call_count = 0
        self.client = anthropic.Anthropic()
        
        # Determine pricing model rates dynamically ($1.00/$5.00 for Haiku 4.5)
        if "haiku-4-5" in model_key:
            self.input_rate = 1.00 / 1_000_000
            self.output_rate = 5.00 / 1_000_000
        else:
            self.input_rate = 0.80 / 1_000_000
            self.output_rate = 4.00 / 1_000_000

    def triage(self, ticket: dict) -> str:
        # Pre-execution guardrail
        if self.spent_usd >= self.budget_usd or self.budget_usd <= 0:
            raise BudgetExceededError("Operational budget limit hit.")
            
        prompt = f"Ticket Title: {ticket.get('title', '')}\nDescription: {ticket.get('description', '')}"

        try:
            message = self.client.messages.create(
                model=self.model_key,
                max_tokens=10,
                messages=[{"role": "user", "content": prompt}]
            )
            
            in_tokens = message.usage.input_tokens
            out_tokens = message.usage.output_tokens
            call_cost = (in_tokens * self.input_rate) + (out_tokens * self.output_rate)
            
            self.spent_usd += call_cost
            self.call_count += 1
            
            return "".join([b.text for b in message.content if hasattr(b, 'text')]).strip().lower()
        except Exception as e:
            if isinstance(e, BudgetExceededError):
                raise e
            return "error_api_failure"


class CodeReviewTriager:
    def __init__(self, model_key: str = "claude-3-5-sonnet-20241022", budget_usd: float = 1.0):
        """
        Handles advanced code review triage requiring deeper contextual reasoning.
        """
        if hasattr(model_key, 'id'):
            self.model_key = model_key.id
        else:
            self.model_key = str(model_key)
            
        self.budget_usd = budget_usd
        self.spent_usd = 0.0
        self.call_count = 0
        self.client = anthropic.Anthropic()
        
        # Sonnet Tiers pricing matrix ($3.00 Input / $15.00 Output per Million)
        self.input_rate = 3.00 / 1_000_000
        self.output_rate = 15.00 / 1_000_000

    def triage(self, request: dict) -> str:
        if self.spent_usd >= self.budget_usd or self.budget_usd <= 0:
            raise BudgetExceededError("Operational budget limit hit for this instance.")
            
        prompt = f"PR Title: {request.get('title', '')}\nDiff: {request.get('description', '')}"
        max_retries = 3
        base_delay = 2.0

        for attempt in range(max_retries + 1):
            try:
                message = self.client.messages.create(
                    model=self.model_key,
                    max_tokens=15,
                    system=(
                      "You are an automated code triage system. Respond with EXACTLY one "
                      "of these three labels in lowercase: approve, request_changes, needs_more_info. "
                      "Do not include explanation, pleasantries, punctuation, or wrapping text."
                    ),
                    messages=[{"role": "user", "content": prompt}]
                )
                
                in_tokens = message.usage.input_tokens
                out_tokens = message.usage.output_tokens
                call_cost = (in_tokens * self.input_rate) + (out_tokens * self.output_rate)
                
                self.spent_usd += call_cost
                self.call_count += 1
                
                return "".join([b.text for b in message.content if hasattr(b, 'text')]).strip().lower()

            except (anthropic.BadRequestError, anthropic.AuthenticationError) as e:
                raise e  # Fail fast on client bugs
                
            except (anthropic.RateLimitError, anthropic.InternalServerError, anthropic.APIStatusError) as e:
                status_code = getattr(e, "status_code", None)
                if status_code and status_code != 429 and status_code < 500:
                    raise e
                if attempt == max_retries:
                    return "needs_more_info"  # degrade gracefully once retries are exhausted
                time.sleep(base_delay * (2 ** attempt))
                
            except anthropic.APIConnectionError as e:
                if attempt == max_retries:
                    return "needs_more_info"  # degrade gracefully once retries are exhausted
                time.sleep(base_delay * (2 ** attempt))

        return "needs_more_info"
