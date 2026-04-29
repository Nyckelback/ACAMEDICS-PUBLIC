"""
Supabase client wrapper for Medical Clinical Cases Telegram Bot.
Handles all database operations and file storage.
"""

import logging
import uuid
from datetime import datetime
from typing import Optional, List, Dict, Any
from supabase import create_client, Client

logger = logging.getLogger(__name__)


class SupabaseClient:
    """Wrapper around Supabase client for database and storage operations."""

    def __init__(self, supabase_url: str, supabase_key: str, service_key: str):
        self.client: Client = create_client(supabase_url, supabase_key)
        self.service_client: Client = create_client(supabase_url, service_key)
        self.url = supabase_url
        self.key = supabase_key
        self.service_key = service_key
        logger.info("Supabase client initialized")

    # ═══════════════════════════════════════════
    # CASES TABLE
    # ═══════════════════════════════════════════

    def save_case(self, parsed_case: Dict[str, Any]) -> Optional[str]:
        """Save a parsed case to the database. Returns UUID."""
        try:
            case_uuid = str(uuid.uuid4())
            case_data = {
                "id": case_uuid,
                "vignette": parsed_case.get("vignette", ""),
                "options": [
                    {"letter": opt.get("letter", ""), "text": opt.get("text", "")}
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
            self.service_client.table("cases").insert(case_data).execute()
            logger.info(f"Case saved with UUID: {case_uuid}")
            return case_uuid
        except Exception as e:
            logger.error(f"Error saving case to database: {e}")
            return None

    def get_case(self, case_uuid: str) -> Optional[Dict[str, Any]]:
        """Retrieve a case from the database."""
        try:
            response = self.client.table("cases").select("*").eq("id", case_uuid).execute()
            if response.data and len(response.data) > 0:
                return response.data[0]
            logger.warning(f"Case not found: {case_uuid}")
            return None
        except Exception as e:
            logger.error(f"Error retrieving case: {e}")
            return None

    def update_case(self, case_uuid: str, data: Dict[str, Any]) -> bool:
        """Update a case in the database."""
        try:
            self.service_client.table("cases").update(data).eq("id", case_uuid).execute()
            logger.info(f"Case updated: {case_uuid}")
            return True
        except Exception as e:
            logger.error(f"Error updating case: {e}")
            return False

    def upload_image(self, file_bytes: bytes, filename: str) -> Optional[str]:
        """Upload an image to Supabase storage. Returns public URL."""
        try:
            unique_filename = f"{uuid.uuid4()}_{filename}"
            bucket_name = "justification-images"
            self.service_client.storage.from_(bucket_name).upload(unique_filename, file_bytes)
            public_url = self.client.storage.from_(bucket_name).get_public_url(unique_filename)
            logger.info(f"Image uploaded: {unique_filename}")
            return public_url
        except Exception as e:
            logger.error(f"Error uploading image: {e}")
            return None

    def get_case_images(self, case_uuid: str) -> List[str]:
        """Get all image URLs for a case."""
        try:
            case = self.get_case(case_uuid)
            if case and "images" in case:
                return case["images"]
            return []
        except Exception as e:
            logger.error(f"Error retrieving case images: {e}")
            return []

    def delete_case(self, case_uuid: str) -> bool:
        """Delete a case from the database."""
        try:
            self.service_client.table("cases").delete().eq("id", case_uuid).execute()
            logger.info(f"Case deleted: {case_uuid}")
            return True
        except Exception as e:
            logger.error(f"Error deleting case: {e}")
            return False

    def get_next_case_number(self) -> int:
        """Get the next case number for display."""
        try:
            response = self.client.table("cases").select("id", count="exact").execute()
            if response.count is not None:
                return response.count + 1
            return 1
        except Exception as e:
            logger.error(f"Error getting next case number: {e}")
            return 1

    # ═══════════════════════════════════════════
    # SCHEDULED POSTS TABLE
    # ═══════════════════════════════════════════

    def schedule_case(self, case_id: str, scheduled_at: datetime, admin_user_id: int) -> Optional[str]:
        """
        Schedule a case for future publication.
        Returns schedule entry UUID if successful.
        """
        try:
            entry_id = str(uuid.uuid4())
            data = {
                "id": entry_id,
                "case_id": case_id,
                "scheduled_at": scheduled_at.isoformat(),
                "status": "pending",
                "admin_user_id": admin_user_id,
            }
            self.service_client.table("scheduled_posts").insert(data).execute()
            logger.info(f"Case {case_id} scheduled for {scheduled_at} (entry {entry_id})")
            return entry_id
        except Exception as e:
            logger.error(f"Error scheduling case: {e}")
            return None

    def get_due_posts(self, now: datetime) -> List[Dict[str, Any]]:
        """
        Get all pending scheduled posts whose time has arrived.
        Returns posts ordered by scheduled_at (earliest first).
        """
        try:
            response = (
                self.service_client.table("scheduled_posts")
                .select("*, cases(*)")
                .eq("status", "pending")
                .lte("scheduled_at", now.isoformat())
                .order("scheduled_at", desc=False)
                .execute()
            )
            return response.data or []
        except Exception as e:
            logger.error(f"Error fetching due posts: {e}")
            return []

    def get_queue(self) -> List[Dict[str, Any]]:
        """
        Get all pending scheduled posts (the full queue), ordered by date.
        Joins with cases table to get vignette preview.
        """
        try:
            response = (
                self.service_client.table("scheduled_posts")
                .select("*, cases(id, vignette, correct_letter, correct_text, published)")
                .eq("status", "pending")
                .order("scheduled_at", desc=False)
                .execute()
            )
            return response.data or []
        except Exception as e:
            logger.error(f"Error fetching queue: {e}")
            return []

    def get_scheduled_post(self, entry_id: str) -> Optional[Dict[str, Any]]:
        """Get a single scheduled post by its ID."""
        try:
            response = (
                self.service_client.table("scheduled_posts")
                .select("*, cases(*)")
                .eq("id", entry_id)
                .execute()
            )
            if response.data:
                return response.data[0]
            return None
        except Exception as e:
            logger.error(f"Error fetching scheduled post {entry_id}: {e}")
            return None

    def mark_publishing(self, entry_id: str) -> bool:
        """Mark a scheduled post as currently publishing (lock)."""
        try:
            self.service_client.table("scheduled_posts").update(
                {"status": "publishing"}
            ).eq("id", entry_id).eq("status", "pending").execute()
            return True
        except Exception as e:
            logger.error(f"Error marking post as publishing: {e}")
            return False

    def mark_done(self, entry_id: str, telegram_message_id: int) -> bool:
        """Mark a scheduled post as successfully published."""
        try:
            self.service_client.table("scheduled_posts").update({
                "status": "done",
                "published_at": datetime.now().isoformat(),
                "telegram_message_id": telegram_message_id,
            }).eq("id", entry_id).execute()
            return True
        except Exception as e:
            logger.error(f"Error marking post as done: {e}")
            return False

    def mark_failed(self, entry_id: str, error_msg: str) -> bool:
        """Mark a scheduled post as failed."""
        try:
            self.service_client.table("scheduled_posts").update({
                "status": "failed",
                "error_message": error_msg[:500],
            }).eq("id", entry_id).execute()
            return True
        except Exception as e:
            logger.error(f"Error marking post as failed: {e}")
            return False

    def cancel_scheduled(self, entry_id: str) -> bool:
        """
        Cancel a scheduled post and delete its case from the DB.
        Only cancels if status is 'pending'.
        """
        try:
            post = self.get_scheduled_post(entry_id)
            if not post:
                return False
            if post["status"] != "pending":
                logger.warning(f"Cannot cancel post {entry_id}: status is {post['status']}")
                return False

            case_id = post["case_id"]

            # Delete the scheduled entry
            self.service_client.table("scheduled_posts").delete().eq("id", entry_id).execute()

            # Delete the case from DB (cleanup)
            self.delete_case(case_id)

            logger.info(f"Scheduled post {entry_id} cancelled + case {case_id} deleted")
            return True
        except Exception as e:
            logger.error(f"Error cancelling scheduled post: {e}")
            return False

    def mark_overdue_as_failed(self, now: datetime) -> List[Dict[str, Any]]:
        """
        On bot startup: mark all pending posts whose time has already passed as 'failed'.
        Returns the list so admin can be notified.
        """
        try:
            response = (
                self.service_client.table("scheduled_posts")
                .select("*, cases(vignette, correct_letter)")
                .eq("status", "pending")
                .lt("scheduled_at", now.isoformat())
                .order("scheduled_at", desc=False)
                .execute()
            )
            overdue = response.data or []

            for post in overdue:
                self.service_client.table("scheduled_posts").update({
                    "status": "failed",
                    "error_message": "Bot estaba offline a la hora programada",
                }).eq("id", post["id"]).execute()

            if overdue:
                logger.warning(f"Marked {len(overdue)} overdue posts as failed on startup")
            return overdue
        except Exception as e:
            logger.error(f"Error marking overdue posts: {e}")
            return []


def init_supabase(url: str, key: str, service_key: str) -> SupabaseClient:
    """Initialize and return a Supabase client."""
    return SupabaseClient(url, key, service_key)
