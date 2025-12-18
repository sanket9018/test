import boto3
import os
import uuid
import base64
import io
from PIL import Image
from typing import Optional, List
from botocore.exceptions import ClientError, NoCredentialsError
import logging

logger = logging.getLogger(__name__)

class S3Manager:
    def __init__(self):
        self.aws_access_key = os.getenv("AWS_ACCESS_KEY_ID")
        self.aws_secret_key = os.getenv("AWS_SECRET_ACCESS_KEY")
        self.bucket_name = os.getenv("AWS_S3_BUCKET", "fit-wit")
        # Default to us-east-1 where the fit-wit bucket lives; can be overridden via AWS_REGION
        self.region = os.getenv("AWS_REGION", "us-east-1")
        
        self.s3_client = boto3.client(
            's3',
            aws_access_key_id=self.aws_access_key,
            aws_secret_access_key=self.aws_secret_key,
            region_name=self.region
        )
    
    def _decode_base64_image(self, base64_string: str) -> tuple[bytes, str]:
        """
        Decode base64 image string and determine file extension
        Returns: (image_bytes, file_extension)
        """
        try:
            # Remove data URL prefix if present
            if base64_string.startswith('data:image/'):
                header, base64_data = base64_string.split(',', 1)
                # Extract format from header (e.g., 'data:image/jpeg;base64')
                format_part = header.split('/')[1].split(';')[0]
                if format_part.lower() == 'jpeg':
                    format_part = 'jpg'
            else:
                base64_data = base64_string
                format_part = 'jpg'  # default
            
            # Decode base64
            image_bytes = base64.b64decode(base64_data)
            
            # Validate it's actually an image using PIL
            try:
                with Image.open(io.BytesIO(image_bytes)) as img:
                    # Convert to RGB if necessary and optimize
                    if img.mode in ('RGBA', 'P'):
                        img = img.convert('RGB')
                    
                    # Resize if too large (max 1024x1024)
                    if img.width > 1024 or img.height > 1024:
                        img.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
                    
                    # Save optimized image
                    output = io.BytesIO()
                    img.save(output, format='JPEG', quality=85, optimize=True)
                    image_bytes = output.getvalue()
                    format_part = 'jpg'
            
            except Exception as e:
                logger.error(f"Invalid image data: {e}")
                raise ValueError("Invalid image format")
            
            return image_bytes, format_part
            
        except Exception as e:
            logger.error(f"Error decoding base64 image: {e}")
            raise ValueError("Invalid base64 image data")
    
    def upload_image(self, base64_image: str, folder: str, user_id: int) -> Optional[str]:
        """
        Upload base64 image to S3
        Returns: S3 key/path if successful, None if failed
        """
        try:
            if not base64_image:
                return None
            
            # Decode image
            image_bytes, file_ext = self._decode_base64_image(base64_image)
            
            # Generate unique filename
            unique_id = str(uuid.uuid4())
            s3_key = f"{folder}/{user_id}/{unique_id}.{file_ext}"
            
            # Upload to S3
            self.s3_client.put_object(
                Bucket=self.bucket_name,
                Key=s3_key,
                Body=image_bytes,
                ContentType=f'image/{file_ext}',
                CacheControl='max-age=31536000'  # 1 year cache
            )
            
            logger.info(f"Successfully uploaded image to S3: {s3_key}")
            return s3_key
            
        except Exception as e:
            logger.error(f"Error uploading image to S3: {e}")
            return None
    
    def delete_image(self, s3_key: str) -> bool:
        """
        Delete image from S3
        Returns: True if successful, False if failed
        """
        try:
            if not s3_key:
                return True
            
            self.s3_client.delete_object(
                Bucket=self.bucket_name,
                Key=s3_key
            )
            
            logger.info(f"Successfully deleted image from S3: {s3_key}")
            return True
            
        except Exception as e:
            logger.error(f"Error deleting image from S3: {e}")
            return False
    
    def delete_multiple_images(self, s3_keys: List[str]) -> bool:
        """
        Delete multiple images from S3 in batch
        Returns: True if all successful, False if any failed
        """
        try:
            if not s3_keys:
                return True
            
            # Filter out empty keys
            valid_keys = [key for key in s3_keys if key]
            if not valid_keys:
                return True
            
            # Prepare delete request
            delete_objects = [{'Key': key} for key in valid_keys]
            
            response = self.s3_client.delete_objects(
                Bucket=self.bucket_name,
                Delete={'Objects': delete_objects}
            )
            
            # Check for errors
            if 'Errors' in response and response['Errors']:
                logger.error(f"Some images failed to delete: {response['Errors']}")
                return False
            
            logger.info(f"Successfully deleted {len(valid_keys)} images from S3")
            return True
            
        except Exception as e:
            logger.error(f"Error deleting multiple images from S3: {e}")
            return False
    
    def get_image_url(self, s3_key: str) -> Optional[str]:
        """
        Get public URL for S3 image
        Returns: Public URL if successful, None if failed
        """
        try:
            if not s3_key:
                return None
            
            # Generate public URL using global S3 endpoint
            url = f"https://{self.bucket_name}.s3.amazonaws.com/{s3_key}"
            return url
            
        except Exception as e:
            logger.error(f"Error generating image URL: {e}")
            return None
    
    def get_presigned_url(self, s3_key: str, expiration: int = 3600) -> Optional[str]:
        """
        Generate presigned URL for private S3 object
        Returns: Presigned URL if successful, None if failed
        """
        try:
            if not s3_key:
                return None
            
            response = self.s3_client.generate_presigned_url(
                'get_object',
                Params={'Bucket': self.bucket_name, 'Key': s3_key},
                ExpiresIn=expiration
            )
            
            return response
            
        except Exception as e:
            logger.error(f"Error generating presigned URL: {e}")
            return None

# Global S3 manager instance
s3_manager = S3Manager()

# Utility functions for easy access
def upload_profile_image(base64_image: str, user_id: int) -> Optional[str]:
    """Upload profile image to S3"""
    return s3_manager.upload_image(base64_image, "profile_pic", user_id)

def upload_transaction_image(base64_image: str, user_id: int) -> Optional[str]:
    """Upload transaction image to S3"""
    return s3_manager.upload_image(base64_image, "transactions", user_id)

def delete_image(s3_key: str) -> bool:
    """Delete single image from S3"""
    return s3_manager.delete_image(s3_key)

def delete_multiple_images(s3_keys: List[str]) -> bool:
    """Delete multiple images from S3"""
    return s3_manager.delete_multiple_images(s3_keys)

def get_image_url(s3_key: str) -> Optional[str]:
    """Get public URL for S3 image"""
    return s3_manager.get_image_url(s3_key)

def get_presigned_image_url(s3_key: str, expiration: int = 3600) -> Optional[str]:
    """Get presigned URL for S3 image"""
    return s3_manager.get_presigned_url(s3_key, expiration)

def delete_user_images(user_id: int) -> bool:
    """Delete all images for a specific user"""
    try:
        # List all objects with user_id prefix
        prefixes = [f"profiles/{user_id}/", f"transactions/{user_id}/"]
        all_keys = []
        
        for prefix in prefixes:
            response = s3_manager.s3_client.list_objects_v2(
                Bucket=s3_manager.bucket_name,
                Prefix=prefix
            )
            
            if 'Contents' in response:
                keys = [obj['Key'] for obj in response['Contents']]
                all_keys.extend(keys)
        
        if all_keys:
            return delete_multiple_images(all_keys)
        
        return True
        
    except Exception as e:
        logger.error(f"Error deleting user images: {e}")
        return False
