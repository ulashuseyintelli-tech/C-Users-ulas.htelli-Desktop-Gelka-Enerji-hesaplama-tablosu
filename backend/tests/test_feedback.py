"""
Sprint 8.7: Feedback Loop Tests

Tests for feedback submission, validation, and stats calculation.

Test Categories:
1. Validation tests (enum, state guard, data validation)
2. UPSERT semantics tests
3. Stats calculation tests (null-safe)
4. Property tests (39-41)
"""

import pytest
from datetime import datetime, timezone, timedelta, date
from unittest.mock import MagicMock, patch

from backend.app.incident_metrics import (
    FeedbackAction,
    IncidentFeedback,
    FeedbackStats,
    FeedbackValidationError,
    validate_feedback,
    submit_feedback,
    g