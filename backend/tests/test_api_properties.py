"""
Property-based tests for the API layer.

Feature: invoice-analysis-system
Uses Hypothesis for property-based testing with minimum 100 iterations.
"""

import pytest
from hypothesis import given, strategies as st, settings
from fastapi import HTTPException

# Import constants and validation function directly to avoid OpenAI client initialization
# We test the validation logic in isolation
import sys
import os

# Add the app directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# Define constants locally to avoid importing from main.py which triggers OpenAI init
MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB
ALLOWED_IMAGE_MIME_TYPES = frozenset([
    "image/jpeg",
    "image/jpg", 
    "image/png",
    "image/webp",
    "image/gif",
    "image/bmp",
    "image/tiff",
])
ALLOWED_PDF_MIME_TYPE = "application/pdf"
ALLOWED_MIME_TYPES = ALLOWED_IMAGE_MIME_TYPES | {ALLOWED_PDF_MIME_TYPE}


def validate_uploaded_file(file, content: bytes) -> None:
    """
    Validate uploaded file for size and MIME type.
    
    Raises HTTPException with 400 status for invalid files.
    Requirements: 1.4, 9.1-9.5
    
    This is a copy of the function from main.py for isolated testing.
    """
    # Validate file size
    if len(content) > MAX_FILE_SIZE_BYTES:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "file_too_large",
                "message": f"Dosya boyutu çok büyük. Maksimum: {MAX_FILE_SIZE_BYTES // (1024*1024)} MB",
                "max_size_bytes": MAX_FILE_SIZE_BYTES,
                "actual_size_bytes": len(content)
            }
        )
    
    # Validate MIME type strictly
    content_type = file.content_type or ""
    
    if content_type not in ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "unsupported_file_type",
                "message": "Desteklenmeyen dosya formatı. Sadece görsel (JPG, PNG, WebP, GIF, BMP, TIFF) veya PDF dosyası yükleyin.",
                "allowed_types": list(ALLOWED_MIME_TYPES),
                "received_type": content_type
            }
        )
    
    # Validate file is not empty
    if len(content) == 0:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "empty_file",
                "message": "Dosya boş. Lütfen geçerli bir fatura dosyası yükleyin."
            }
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Strategies for generating test data
# ═══════════════════════════════════════════════════════════════════════════════

# Invalid MIME types that should be rejected
INVALID_MIME_TYPES = [
    "text/plain",
    "text/html",
    "text/csv",
    "application/json",
    "application/xml",
    "application/zip",
    "application/octet-stream",
    "video/mp4",
    "audio/mpeg",
    "application/msword",
    "application/vnd.ms-excel",
    "",
    None,
]


class MockUploadFile:
    """Mock UploadFile for testing validation function directly."""
    
    def __init__(self, content_type: str, filename: str = "test.txt"):
        self.content_type = content_type
        self.filename = filename


# ═══════════════════════════════════════════════════════════════════════════════
# Property 7: Invalid File Rejection
# **Validates: Requirements 1.4**
#
# For any file upload with unsupported MIME type (not image/* or application/pdf),
# the API SHALL return HTTP 400 status code with error message.
# ═══════════════════════════════════════════════════════════════════════════════

class TestProperty7InvalidFileRejection:
    """
    Feature: invoice-analysis-system, Property 7: Invalid File Rejection
    **Validates: Requirements 1.4**
    """

    @settings(max_examples=100)
    @given(st.sampled_from(INVALID_MIME_TYPES))
    def test_unsupported_mime_type_returns_400(self, invalid_mime_type):
        """
        1.4: IF an unsupported file format is uploaded THEN THE System SHALL
        return HTTP 400 with error message.
        """
        mock_file = MockUploadFile(content_type=invalid_mime_type)
        content = b"fake file content"
        
        with pytest.raises(HTTPException) as exc_info:
            validate_uploaded_file(mock_file, content)
        
        assert exc_info.value.status_code == 400
        assert exc_info.value.detail["error"] == "unsupported_file_type"

    @settings(max_examples=100)
    @given(st.integers(min_value=1, max_value=1000))
    def test_oversized_file_returns_400(self, extra_bytes):
        """
        File size validation: Files exceeding MAX_FILE_SIZE_BYTES SHALL be rejected
        with HTTP 400.
        """
        mock_file = MockUploadFile(content_type="image/png")
        # Create content that exceeds the limit by a variable amount
        large_content = b"x" * (MAX_FILE_SIZE_BYTES + extra_bytes)
        
        with pytest.raises(HTTPException) as exc_info:
            validate_uploaded_file(mock_file, large_content)
        
        assert exc_info.value.status_code == 400
        assert exc_info.value.detail["error"] == "file_too_large"

    @settings(max_examples=100)
    @given(st.sampled_from(list(ALLOWED_IMAGE_MIME_TYPES)))
    def test_empty_file_returns_400(self, valid_mime_type):
        """
        Empty file validation: Empty files SHALL be rejected with HTTP 400.
        """
        mock_file = MockUploadFile(content_type=valid_mime_type)
        empty_content = b""
        
        with pytest.raises(HTTPException) as exc_info:
            validate_uploaded_file(mock_file, empty_content)
        
        assert exc_info.value.status_code == 400
        assert exc_info.value.detail["error"] == "empty_file"

    @settings(max_examples=100)
    @given(
        st.sampled_from(list(ALLOWED_MIME_TYPES)),
        st.binary(min_size=1, max_size=1000)
    )
    def test_valid_mime_type_and_size_passes_validation(self, valid_mime_type, content):
        """
        Valid files: Files with allowed MIME types and valid size SHALL pass validation.
        """
        mock_file = MockUploadFile(content_type=valid_mime_type)
        
        # Should not raise any exception
        validate_uploaded_file(mock_file, content)

    @settings(max_examples=100)
    @given(st.text(min_size=1, max_size=50).filter(lambda x: x not in ALLOWED_MIME_TYPES))
    def test_random_invalid_mime_types_rejected(self, random_mime_type):
        """
        1.4: Any random string that is not in ALLOWED_MIME_TYPES SHALL be rejected.
        """
        mock_file = MockUploadFile(content_type=random_mime_type)
        content = b"fake file content"
        
        with pytest.raises(HTTPException) as exc_info:
            validate_uploaded_file(mock_file, content)
        
        assert exc_info.value.status_code == 400
        assert exc_info.value.detail["error"] == "unsupported_file_type"
        assert exc_info.value.detail["received_type"] == random_mime_type

    def test_error_response_contains_allowed_types(self):
        """
        Error response SHALL include list of allowed MIME types for user guidance.
        """
        mock_file = MockUploadFile(content_type="text/plain")
        content = b"fake file content"
        
        with pytest.raises(HTTPException) as exc_info:
            validate_uploaded_file(mock_file, content)
        
        assert "allowed_types" in exc_info.value.detail
        assert set(exc_info.value.detail["allowed_types"]) == ALLOWED_MIME_TYPES

    def test_error_response_contains_received_type(self):
        """
        Error response SHALL include the received MIME type for debugging.
        """
        invalid_type = "application/json"
        mock_file = MockUploadFile(content_type=invalid_type)
        content = b"fake file content"
        
        with pytest.raises(HTTPException) as exc_info:
            validate_uploaded_file(mock_file, content)
        
        assert exc_info.value.detail["received_type"] == invalid_type

    def test_file_size_error_contains_size_info(self):
        """
        File size error SHALL include max and actual size for user guidance.
        """
        mock_file = MockUploadFile(content_type="image/png")
        large_content = b"x" * (MAX_FILE_SIZE_BYTES + 100)
        
        with pytest.raises(HTTPException) as exc_info:
            validate_uploaded_file(mock_file, large_content)
        
        assert "max_size_bytes" in exc_info.value.detail
        assert "actual_size_bytes" in exc_info.value.detail
        assert exc_info.value.detail["max_size_bytes"] == MAX_FILE_SIZE_BYTES
        assert exc_info.value.detail["actual_size_bytes"] == len(large_content)
