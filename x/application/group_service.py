from __future__ import annotations
import secrets
import time
from typing import List, Optional

from x.domain.group import Group, GroupId
from x.infrastructure.repositories.base import GroupRepository


class GroupService:
    def __init__(self, group_repo: GroupRepository):
        self._groups = group_repo

    def create_group(
        self,
        phone: str,
        suffix: str,
        *,
        tunnel_secret: Optional[str] = None,
        ts: Optional[int] = None,
    ) -> Group:
        gid = GroupId.of(phone, suffix)
        secret = tunnel_secret or ("tun-" + secrets.token_urlsafe(32))
        ts = ts or int(time.time())
        group = Group(
            group_id=gid, phone=phone, suffix=suffix,
            tunnel_secret=secret, created_at=ts,
        )
        self._groups.save(group)
        return group

    def get_group(self, group_id: str) -> Optional[Group]:
        return self._groups.get(group_id)

    def list_groups(self, phone: Optional[str] = None) -> List[Group]:
        return self._groups.list_by_phone(phone)
