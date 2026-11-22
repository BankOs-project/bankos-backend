"""Operaciones para el perfil propio, incluida su foto privada."""

from io import BytesIO

from fastapi import APIRouter, Depends, Request, Response
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.core.database import get_db
from app.core.exceptions import BankOSException
from app.core.responses import success_response
from app.modules.auth_users.application.dtos import (
    StartProfileEmailChangeDTO, UpdateProfileDTO, VerifyProfileEmailChangeDTO,
)
from app.modules.auth_users.application.profile import EMAIL_CHANGE_TTL_SECONDS, ProfileService
from app.modules.auth_users.presentation.dependencies import AuthenticatedUser, get_current_user

router = APIRouter(prefix="/me")
PRIVATE_HEADERS = {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"}
PHOTO_FORMATS = {"image/jpeg": "JPEG", "image/png": "PNG"}
PHOTO_MAX_BYTES = 2 * 1024 * 1024
PHOTO_MAX_DIMENSION = 2048


def profile_service(
    actor: AuthenticatedUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> ProfileService:
    return ProfileService(session, actor)


def private_response(data: dict, message: str, code: str, status_code: int = 200) -> Response:
    response = success_response(data, message, code, status_code)
    response.headers.update(PRIVATE_HEADERS)
    return response


@router.get("")
async def get_my_profile(service: ProfileService = Depends(profile_service)):
    return private_response(await service.get(), "Perfil obtenido exitosamente", "USER_PROFILE")


@router.patch("")
async def update_my_profile(dto: UpdateProfileDTO, service: ProfileService = Depends(profile_service)):
    return private_response(await service.update(dto), "Perfil actualizado", "USER_PROFILE_UPDATED")


@router.get("/photo")
async def get_my_photo(service: ProfileService = Depends(profile_service)):
    media_type, content = await service.get_photo()
    return Response(content, media_type=media_type, headers=PRIVATE_HEADERS)


def normalize_photo(content: bytearray, image_format: str) -> bytes:
    try:
        with Image.open(BytesIO(content), formats=(image_format,)) as source:
            if max(source.size) > PHOTO_MAX_DIMENSION or getattr(source, "n_frames", 1) != 1:
                raise BankOSException("La foto debe ser estática y de hasta 2048 × 2048", "PROFILE_PHOTO_INVALID", 422)
            # Aplicar orientación y conservar solo los píxeles, sin EXIF, GPS ni texto.
            oriented = ImageOps.exif_transpose(source)
            has_transparency = "A" in oriented.getbands() or "transparency" in oriented.info
            mode = "RGBA" if image_format == "PNG" and has_transparency else "RGB"
            pixels = oriented.convert(mode)
            clean = Image.frombytes(mode, pixels.size, pixels.tobytes())
            buffer = BytesIO()
            clean.save(buffer, format=image_format)
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, SyntaxError, ValueError):
        raise BankOSException("La foto no es una imagen válida del tipo indicado", "PROFILE_PHOTO_INVALID", 422) from None
    if buffer.tell() > PHOTO_MAX_BYTES:
        raise BankOSException("La foto supera el límite de 2 MiB", "PROFILE_PHOTO_TOO_LARGE", 413)
    return buffer.getvalue()


@router.put(
    "/photo",
    openapi_extra={"requestBody": {"required": True, "content": {
        media_type: {"schema": {"type": "string", "format": "binary"}} for media_type in PHOTO_FORMATS
    }}},
)
async def upload_my_photo(request: Request, service: ProfileService = Depends(profile_service)):
    media_type = request.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
    if media_type not in PHOTO_FORMATS:
        raise BankOSException("Sube una imagen JPEG o PNG", "PROFILE_PHOTO_UNSUPPORTED_TYPE", 415)
    content = bytearray()
    async for chunk in request.stream():
        if len(content) + len(chunk) > PHOTO_MAX_BYTES:
            raise BankOSException("La foto supera el límite de 2 MiB", "PROFILE_PHOTO_TOO_LARGE", 413)
        content.extend(chunk)
    photo = await run_in_threadpool(normalize_photo, content, PHOTO_FORMATS[media_type])
    return private_response(await service.set_photo(media_type, photo), "Foto actualizada", "PROFILE_PHOTO_UPDATED")


@router.delete("/photo")
async def delete_my_photo(service: ProfileService = Depends(profile_service)):
    return private_response(await service.set_photo(None, None), "Foto eliminada", "PROFILE_PHOTO_REMOVED")


@router.post("/email-change", status_code=202)
async def start_email_change(dto: StartProfileEmailChangeDTO, service: ProfileService = Depends(profile_service)):
    request_id = await service.start_email_change(dto)
    return private_response(
        {"request_id": str(request_id), "expires_in": EMAIL_CHANGE_TTL_SECONDS},
        "Revisa tu nuevo correo para confirmar el cambio", "PROFILE_EMAIL_PENDING", 202,
    )


@router.post("/email-change/verify")
async def verify_email_change(dto: VerifyProfileEmailChangeDTO, service: ProfileService = Depends(profile_service)):
    return private_response(await service.verify_email_change(dto), "Correo actualizado", "PROFILE_EMAIL_UPDATED")
