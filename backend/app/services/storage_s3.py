"""
S3/MinIO Storage Backend.

For production deployments with object storage.
"""
import logging
from typing import Optional

from app.core.config import settings
from app.services.storage_backend import StorageBackend

logger = logging.getLogger(__name__)


class S3Storage(StorageBackend):
    """S3/MinIO object storage."""

    def __init__(self):
        try:
            import boto3
            from botocore.exceptions import ClientError
            self._ClientError = ClientError
        except ImportError:
            raise RuntimeError("boto3 required for S3 storage. Install: pip install boto3")

        self.s3 = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region,
        )
        self.bucket = settings.s3_bucket
        
        # Ensure bucket exists (MinIO auto-create)
        self._ensure_bucket()
        
        logger.info(f"S3Storage initialized: bucket={self.bucket}, endpoint={settings.s3_endpoint_url}")

    def _ensure_bucket(self):
        """Create bucket if it doesn't exist (MinIO friendly)."""
        try:
            self.s3.head_bucket(Bucket=self.bucket)
            logger.debug(f"Bucket exists: {self.bucket}")
        except self._ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', '')
            if error_code in ('404', 'NoSuchBucket'):
                try:
                    # MinIO'da region şart değil
                    if settings.s3_endpoint_url:
                        # MinIO - basit create
                        self.s3.create_bucket(Bucket=self.bucket)
                    else:
                        # AWS S3 - region gerekli
                        self.s3.create_bucket(
                            Bucket=self.bucket,
                            CreateBucketConfiguration={'LocationConstraint': settings.s3_region}
                        )
                    logger.info(f"Created bucket: {self.bucket}")
                except Exception as create_err:
                    logger.warning(f"Could not create bucket {self.bucket}: {create_err}")
            else:
                logger.warning(f"Bucket check failed: {e}")

    def put_bytes(self, key: str, data: bytes, content_type: str) -> str:
        """Store bytes to S3/MinIO."""
        self.s3.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=data,
            ContentType=content_type
        )
        ref = f"s3://{self.bucket}/{key}"
        logger.debug(f"Stored {len(data)} bytes to {ref}")
        return ref

    def get_bytes(self, ref: str) -> bytes:
        """Read bytes from S3/MinIO."""
        bucket, key = self._parse_ref(ref)
        obj = self.s3.get_object(Bucket=bucket, Key=key)
        return obj["Body"].read()

    def exists(self, ref: str) -> bool:
        """Check if object exists."""
        try:
            bucket, key = self._parse_ref(ref)
            self.s3.head_object(Bucket=bucket, Key=key)
            return True
        except self._ClientError:
            return False

    def delete(self, ref: str) -> bool:
        """Delete object."""
        try:
            bucket, key = self._parse_ref(ref)
            self.s3.delete_object(Bucket=bucket, Key=key)
            return True
        except Exception as e:
            logger.error(f"Delete failed for {ref}: {e}")
            return False

    def get_presigned_url(self, ref: str, expires_in: int = 300) -> str:
        """
        Generate presigned URL for download.
        
        Args:
            ref: S3 reference (s3://bucket/key)
            expires_in: URL validity in seconds (default 5 minutes)
        
        Returns:
            Presigned URL string
        """
        bucket, key = self._parse_ref(ref)
        return self.s3.generate_presigned_url(
            'get_object',
            Params={'Bucket': bucket, 'Key': key},
            ExpiresIn=expires_in
        )

    def _parse_ref(self, ref: str) -> tuple[str, str]:
        """Parse s3://bucket/key reference."""
        if not ref.startswith("s3://"):
            raise ValueError(f"Invalid S3 reference: {ref}")
        _, _, rest = ref.partition("s3://")
        bucket, _, key = rest.partition("/")
        return bucket, key
