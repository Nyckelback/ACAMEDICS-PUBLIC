"""
Supabase client wrapper for Medical Clinical Cases Telegram Bot.
Handles all database operations and file storage.
"""

import logging
import uuid
from typing import Optional, List, Dict, Any
from supabase import create_client, Client

logger = logging.getLogger(__name__)


class SupabaseClient:
    """Wrapper around Supabase client for database and storage operations."""

    def __init__(self, supabase_url: str, supabase_key: str, service_key: str):
        """
        Initialize Supabase client.

        Args:
            supabase_url: Supabase project URL
            supabase_key: Supabase anon key
            service_key: Supabase service role key for writes
        """
        self.client: Client = create_client(supabase_url, supabase_key)
        self.service_client: Client = create_client(supabase_url, service_key)
        self.url = supabase_url
        self.key = supabase_key
        self.service_key = service_key
        logger.info("Supabase client initialized")

    def save_case(self, parsed_case: Dict[str, Any]) -> Optional[str]:
        """
        Save a parsed case to the database.

        Args:
            parsed_case: Dictionary with case data from parser

        Returns:
            Case UUID if successful, None otherwise
        """
        try:
            case_uuid = str(uuid.uuid4())

            # Prepare case data for database
            case_data = {
                "id": case_uuid,
                "vignette": parsed_case.get("vignette", ""),
                "options": [
                    {
                        "letter": opt.get("letter", ""),
                        "text": opt.get("text", "")
                    }
                    for opt in parsed_case.get("options", [])
                ],
                "correct_letter": parsed_case.get("correct_letter", ""),
                "correct_text": parsed_case.get("correct_text", ""),
                "justification": parsed_case.get("justification", ""),
                "tip": parsed_case.get("tip", ""),
                "bibliography": parsed_case.get("bibliography", []),
                "images": parsed_case.get("images", []),
                "published": False,
                "telegram_message_id": None,
            }

            response = self.service_client.table("cases").insert(case_data).execute()
            logger.info(f"Case saved with UUID: {case_uuid}")
            return case_uuid
        except Exception as e:
            logger.error(f"Error saving case to database: {e}")
            return None

    def get_case(self, case_uuid: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve a case from the database.

        Args:
            case_uuid: Case UUID

        Returns:
            Case data if found, None otherwise
        """
        try:
            response = self.client.table("cases").select("*").eq("id", case_uuid).execute()
            if response.data and len(response.data) > 0:
                logger.info(f"Case retrieved: {case_uuid}")
                return response.data[0]
            logger.warning(f"Case not found: {case_uuid}")
            return None
        except Exception as e:
            logger.error(f"Error retrieving case: {e}")
            return None

    def update_case(self, case_uuid: str, data: Dict[str, Any]) -> bool:
        """
        Update a case in the database.

        Args:
            case_uuid: Case UUID
            data: Dictionary of fields to update

        Returns:
            True if successful, False otherwise
        """
        try:
            response = self.service_client.table("cases").update(data).eq("id", case_uuid).execute()
            logger.info(f"Case updated: {case_uuid}")
            return True
        except Exception as e:
            logger.error(f"Error updating case: {e}")
            return False

    def upload_image(self, file_bytes: bytes, filename: str) -> Optional[str]:
        """
        Upload an image to Supabase storage.

        Args:
            file_bytes: Image file bytes
            filename: Original filename

        Returns:
            Public URL if successful, None otherwise
        """
        try:
            # Generate unique filename
            unique_filename = f"{uuid.uuid4()}_{filename}"
            bucket_name = "justification-images"

            # Upload to storage
            response = self.service_client.storage.from_(bucket_name).upload(
                unique_filename, file_bytes
            )

            # Get public URL
            public_url = self.client.storage.from_(bucket_name).get_public_url(unique_filename)
            logger.info(f"Image uploaded: {unique_filename}")
            return public_url
        except Exception as e:
            logger.error(f"Error uploading image: {e}")
            return None

    def get_case_images(self, case_uuid: str) -> List[str]:
        """
        Get all image URLs for a case.

        Args:
            case_uuid: Case UUID

        Returns:
            List of image URLs
        """
        try:
            case = self.get_case(case_uuid)
            if case and "images" in case:
                return case["images"]
            return []
        except Exception as e:
            logger.error(f"Error retrieving case images: {e}")
            return []

    def get_next_case_number(self) -> int:
        """
        Get the next case number for display.

        Returns:
            Next available case number
        """
        try:
            response = self.client.table("cases").select("id", count="exact").execute()
            if response.count is not None:
                return response.count + 1
            return 1
        except Exception as e:
            logger.error(f"Error getting next case number: {e}")
            return 1


def init_supabase(url: str, key: str, service_key: str) -> SupabaseClient:
    """
    Initialize and return a Supabase client.

    Args:
        url: Supabase URL
        key: Supabase anon key
        service_key: Supabase service role key

    Returns:
        SupabaseClient instance
    """
    return SupabaseClient(url, key, service_key)
