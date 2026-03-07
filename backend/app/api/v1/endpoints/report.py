"""
Live Report v1 API Endpoints

POST /api/v1/reports/{video_id}/generate  - Generate live report for a video
GET  /api/v1/reports/{video_id}           - Get latest live report
"""

import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db, get_current_user
from app.services.live_report_service import generate_live_report

logger = logging.getLogger("report_api")

router = APIRouter(prefix="/reports", tags=["Reports"])


@router.post("/{video_id}/generate")
async def generate_report(
    video_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Generate Live Report v1 for a video.
    Reads all video_phases, scores them, and produces:
    - strong_segments TOP 3
    - weak_segments TOP 3
    - suggestions TOP 3
    Saves the report to the reports table.
    """
    try:
        # 1. Verify video ownership
        video_sql = text("""
            SELECT v.id, v.user_id, v.original_filename, v.status
            FROM videos v
            WHERE v.id = :video_id
        """)
        vres = await db.execute(video_sql, {"video_id": video_id})
        video_row = vres.fetchone()
        if not video_row:
            raise HTTPException(status_code=404, detail="Video not found")
        if current_user and current_user.get("id") != video_row.user_id:
            raise HTTPException(status_code=403, detail="Forbidden")

        # 2. Fetch all video_phases with metrics
        # Try with cta_score/audio_features first, fallback without
        phases_sql = text("""
            SELECT
                vp.phase_index,
                vp.phase_description,
                vp.time_start,
                vp.time_end,
                COALESCE(vp.gmv, 0) as gmv,
                COALESCE(vp.order_count, 0) as order_count,
                COALESCE(vp.viewer_count, 0) as viewer_count,
                COALESCE(vp.like_count, 0) as like_count,
                COALESCE(vp.comment_count, 0) as comment_count,
                COALESCE(vp.share_count, 0) as share_count,
                COALESCE(vp.new_followers, 0) as new_followers,
                COALESCE(vp.product_clicks, 0) as product_clicks,
                COALESCE(vp.conversion_rate, 0) as conversion_rate,
                COALESCE(vp.gpm, 0) as gpm,
                COALESCE(vp.importance_score, 0) as importance_score,
                vp.product_names,
                vp.user_rating,
                vp.user_comment,
                vp.cta_score,
                vp.audio_features
            FROM video_phases vp
            WHERE vp.video_id = :video_id
            ORDER BY vp.phase_index ASC
        """)

        try:
            result = await db.execute(phases_sql, {"video_id": video_id})
        except Exception:
            # Fallback without cta_score/audio_features
            await db.rollback()
            phases_sql = text("""
                SELECT
                    vp.phase_index,
                    vp.phase_description,
                    vp.time_start,
                    vp.time_end,
                    COALESCE(vp.gmv, 0) as gmv,
                    COALESCE(vp.order_count, 0) as order_count,
                    COALESCE(vp.viewer_count, 0) as viewer_count,
                    COALESCE(vp.like_count, 0) as like_count,
                    COALESCE(vp.comment_count, 0) as comment_count,
                    COALESCE(vp.share_count, 0) as share_count,
                    COALESCE(vp.new_followers, 0) as new_followers,
                    COALESCE(vp.product_clicks, 0) as product_clicks,
                    COALESCE(vp.conversion_rate, 0) as conversion_rate,
                    COALESCE(vp.gpm, 0) as gpm,
                    COALESCE(vp.importance_score, 0) as importance_score,
                    vp.product_names,
                    vp.user_rating,
                    vp.user_comment,
                    NULL as cta_score,
                    NULL as audio_features
                FROM video_phases vp
                WHERE vp.video_id = :video_id
                ORDER BY vp.phase_index ASC
            """)
            result = await db.execute(phases_sql, {"video_id": video_id})

        rows = result.fetchall()
        if not rows:
            raise HTTPException(
                status_code=404,
                detail="No phases found for this video. Please process the video first.",
            )

        # Convert rows to dicts
        phases = []
        for r in rows:
            phases.append({
                "phase_index": r.phase_index,
                "phase_description": r.phase_description,
                "time_start": r.time_start,
                "time_end": r.time_end,
                "gmv": r.gmv,
                "order_count": r.order_count,
                "viewer_count": r.viewer_count,
                "like_count": r.like_count,
                "comment_count": r.comment_count,
                "share_count": r.share_count,
                "new_followers": r.new_followers,
                "product_clicks": r.product_clicks,
                "conversion_rate": r.conversion_rate,
                "gpm": r.gpm,
                "importance_score": r.importance_score,
                "product_names": r.product_names,
                "user_rating": r.user_rating,
                "user_comment": r.user_comment,
                "cta_score": r.cta_score,
                "audio_features": r.audio_features,
            })

        # 3. Generate report
        report = generate_live_report(phases)

        # 4. Save to reports table
        report_json = json.dumps(report, ensure_ascii=False, default=str)

        # Check for existing report
        existing = await db.execute(
            text("SELECT id FROM reports WHERE video_id = :vid ORDER BY version DESC LIMIT 1"),
            {"vid": video_id},
        )
        existing_row = existing.fetchone()

        if existing_row:
            # Update version
            current_version = await db.execute(
                text("SELECT COALESCE(MAX(version), 0) FROM reports WHERE video_id = :vid"),
                {"vid": video_id},
            )
            max_version = current_version.scalar() or 0
            new_version = max_version + 1
        else:
            new_version = 1

        import uuid
        await db.execute(
            text("""
                INSERT INTO reports (id, video_id, report_content, version, start_time, end_time)
                VALUES (:id, :vid, :content, :version, :start_time, :end_time)
            """),
            {
                "id": str(uuid.uuid4()),
                "vid": video_id,
                "content": report_json,
                "version": new_version,
                "start_time": datetime.now(timezone.utc),
                "end_time": datetime.now(timezone.utc),
            },
        )
        await db.commit()

        logger.info(
            "Generated Live Report v%d for video %s: %d strong, %d weak, %d suggestions",
            new_version,
            video_id,
            len(report["strong_segments"]),
            len(report["weak_segments"]),
            len(report["suggestions"]),
        )

        return {
            "video_id": video_id,
            "version": new_version,
            "report": report,
        }

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(f"Failed to generate report: {exc}")
        raise HTTPException(status_code=500, detail=f"Failed to generate report: {exc}")


@router.get("/{video_id}")
async def get_report(
    video_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Get the latest Live Report for a video.
    """
    try:
        # Verify video ownership
        video_sql = text("""
            SELECT v.id, v.user_id
            FROM videos v
            WHERE v.id = :video_id
        """)
        vres = await db.execute(video_sql, {"video_id": video_id})
        video_row = vres.fetchone()
        if not video_row:
            raise HTTPException(status_code=404, detail="Video not found")
        if current_user and current_user.get("id") != video_row.user_id:
            raise HTTPException(status_code=403, detail="Forbidden")

        # Get latest report
        report_sql = text("""
            SELECT id, report_content, version, start_time, end_time
            FROM reports
            WHERE video_id = :vid
            ORDER BY version DESC
            LIMIT 1
        """)
        result = await db.execute(report_sql, {"vid": video_id})
        row = result.fetchone()

        if not row:
            return {
                "video_id": video_id,
                "version": 0,
                "report": None,
                "message": "No report generated yet. Call POST /generate to create one.",
            }

        report_data = json.loads(row.report_content) if isinstance(row.report_content, str) else row.report_content

        return {
            "video_id": video_id,
            "version": row.version,
            "report": report_data,
        }

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(f"Failed to get report: {exc}")
        raise HTTPException(status_code=500, detail=f"Failed to get report: {exc}")
