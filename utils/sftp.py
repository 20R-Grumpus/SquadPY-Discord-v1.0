"""SFTP helpers (upload, download, multi-file modify)."""

import os
import asyncio

import paramiko

from config import logger, BACKUP_DIR

# --- SFTP Helper Functions ---


def _sftp_write_content_sync(
    sftp_host, sftp_port, sftp_user, sftp_password, remote_path, content
):
    logger.info(f"Starting SFTP upload to {sftp_host}:{sftp_port} → {remote_path}")
    transport = None
    sftp = None
    temp_file_path = "temp_sftp_upload.txt"

    try:
        if not remote_path:
            raise ValueError("remote_path is None or empty")

        logger.debug(f"Writing {len(content)} bytes to temp file")
        with open(temp_file_path, "w") as f:
            f.write(content)

        logger.debug(f"Connecting to SFTP: {sftp_user}@{sftp_host}")
        transport = paramiko.Transport((sftp_host, sftp_port))
        transport.connect(username=sftp_user, password=sftp_password)
        sftp = paramiko.SFTPClient.from_transport(transport)

        logger.debug(f"Uploading file to {remote_path}")
        sftp.put(temp_file_path, remote_path)

        logger.info(f"SFTP upload successful: {remote_path}")
        return True  # ✅ SUCCESS

    except Exception as e:
        logger.error(f"SFTP upload failed: {e}", exc_info=True)
        return False  # ❌ FAILURE

    finally:
        if sftp:
            sftp.close()
            logger.debug("SFTP connection closed")
        if transport:
            transport.close()
            logger.debug("Transport connection closed")
        if os.path.exists(temp_file_path):
            os.remove(temp_file_path)
            logger.debug("Temp file cleaned up")


async def sftp_write_content(
    sftp_host, sftp_port, sftp_user, sftp_password, remote_path, content
):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None,
        _sftp_write_content_sync,
        sftp_host,
        sftp_port,
        sftp_user,
        sftp_password,
        remote_path,
        content,
    )


def _sftp_download_sync(
    sftp_host, sftp_port, sftp_user, sftp_password, remote_path, local_path
):
    """Synchronous SFTP download - runs in thread executor."""
    logger.info(f"Starting SFTP download from {sftp_host}:{sftp_port} → {remote_path}")
    transport = None
    sftp = None

    try:
        logger.debug(f"Connecting to SFTP: {sftp_user}@{sftp_host}")
        transport = paramiko.Transport((sftp_host, sftp_port))
        transport.connect(username=sftp_user, password=sftp_password)
        sftp = paramiko.SFTPClient.from_transport(transport)

        logger.debug(f"Downloading file from {remote_path} to {local_path}")
        sftp.get(remote_path, local_path)

        logger.info(f"SFTP download successful: {remote_path}")
        return True  # ✅ SUCCESS

    except Exception as e:
        logger.error(f"SFTP download failed: {e}", exc_info=True)
        return False  # ❌ FAILURE

    finally:
        if sftp:
            sftp.close()
            logger.debug("SFTP connection closed")
        if transport:
            transport.close()
            logger.debug("Transport connection closed")


async def sftp_download(
    sftp_host, sftp_port, sftp_user, sftp_password, remote_path, local_path
):
    """Async wrapper for SFTP download using thread executor."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None,
        _sftp_download_sync,
        sftp_host,
        sftp_port,
        sftp_user,
        sftp_password,
        remote_path,
        local_path,
    )


def _sftp_modify(server, modify_map=None, backup_name=None):
    host = server["host"]
    port = server.get("port", 22)

    logger.info(f"[SFTP] Connecting to {host}:{port}")

    try:
        transport = paramiko.Transport((host, port))
        transport.connect(username=server["username"], password=server["password"])
        sftp = paramiko.SFTPClient.from_transport(transport)
        logger.info(f"[SFTP] Connected to {host}:{port}")

        # List root directory to check session view
        try:
            root_listing = sftp.listdir(".")
            logger.info(f"[SFTP] Session root contents: {root_listing}")
        except Exception as e:
            logger.warning(f"[SFTP] Could not list session root: {e}")

        for path, modify_func in (modify_map or {}).items():
            logger.info(f"[SFTP] Processing file: {path}")

            try:
                # Check if file exists
                try:
                    attrs = sftp.stat(path)
                    logger.info(f"[SFTP] {path} exists, size={attrs.st_size}")
                except FileNotFoundError:
                    logger.warning(f"[SFTP] {path} not found, will start empty")
                    attrs = None

                # Read current content for backup
                content = ""
                if attrs:
                    with sftp.open(path, "r") as f:
                        content = f.read().decode()
                    logger.info(f"[SFTP] Read {len(content)} bytes from {path}")

                # Backup
                if backup_name:
                    safe_name = path.split("/")[-1]
                    backup_path = BACKUP_DIR / f"{backup_name}_{host}_{safe_name}"
                    with open(backup_path, "w", encoding="utf-8") as f:
                        f.write(content)
                    logger.info(f"[SFTP] Backup saved: {backup_path}")

                # Apply modification
                if modify_func:
                    new_content = modify_func(content)
                    try:
                        with sftp.open(path, "w") as f:
                            f.write(new_content)
                        logger.info(f"[SFTP] Updated {path} on {host}")
                    except Exception as e:
                        logger.error(f"[SFTP] Failed to write {path} on {host} - {e}")
                        raise

            except Exception as e:
                logger.error(f"[SFTP] Error processing {path} on {host}: {e}")

        sftp.close()
        transport.close()
        logger.info(f"[SFTP] Closed connection to {host}")
        return "ok"

    except Exception as e:
        logger.error(f"[SFTP] Connection failed to {host}:{port} - {e}")
        return f"error: {e}"


async def sftp_modify_async(server, modify_map=None, backup_name=None):
    return await asyncio.to_thread(_sftp_modify, server, modify_map, backup_name)
