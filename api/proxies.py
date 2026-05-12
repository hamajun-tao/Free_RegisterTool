from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlmodel import Session, select
from pydantic import BaseModel
from typing import Optional
from core.db import ProxyModel, get_session
from core.proxy_pool import proxy_pool
from core.smart_proxy_selector import smart_selector

router = APIRouter(prefix="/proxies", tags=["proxies"])


class ProxyCreate(BaseModel):
    url: str
    region: str = ""


class ProxyBulkCreate(BaseModel):
    proxies: list[str]
    region: str = ""


@router.get("")
def list_proxies(session: Session = Depends(get_session)):
    items = session.exec(select(ProxyModel)).all()
    return items


@router.post("")
def add_proxy(body: ProxyCreate, session: Session = Depends(get_session)):
    existing = session.exec(select(ProxyModel).where(ProxyModel.url == body.url)).first()
    if existing:
        raise HTTPException(400, "代理已存在")
    p = ProxyModel(url=body.url, region=body.region)
    session.add(p)
    session.commit()
    session.refresh(p)
    return p


@router.post("/bulk")
def bulk_add_proxies(body: ProxyBulkCreate, session: Session = Depends(get_session)):
    added = 0
    for url in body.proxies:
        url = url.strip()
        if not url:
            continue
        existing = session.exec(select(ProxyModel).where(ProxyModel.url == url)).first()
        if not existing:
            session.add(ProxyModel(url=url, region=body.region))
            added += 1
    session.commit()
    return {"added": added}


@router.delete("/{proxy_id}")
def delete_proxy(proxy_id: int, session: Session = Depends(get_session)):
    p = session.get(ProxyModel, proxy_id)
    if not p:
        raise HTTPException(404, "代理不存在")
    session.delete(p)
    session.commit()
    return {"ok": True}


@router.patch("/{proxy_id}/toggle")
def toggle_proxy(proxy_id: int, session: Session = Depends(get_session)):
    p = session.get(ProxyModel, proxy_id)
    if not p:
        raise HTTPException(404, "代理不存在")
    p.is_active = not p.is_active
    session.add(p)
    session.commit()
    return {"is_active": p.is_active}


@router.post("/check")
def check_proxies(background_tasks: BackgroundTasks):
    background_tasks.add_task(proxy_pool.check_all)
    return {"message": "检测任务已启动"}


@router.get("/cooldown/status")
def get_cooldown_status():
    """获取所有代理的冷却状态"""
    status = proxy_pool.get_cooldown_status()
    return {
        "cooldown_seconds": proxy_pool._cooldown_seconds,
        "proxies": status,
        "total": len(status),
        "cooling_down": sum(1 for s in status.values() if s["cooling_down"])
    }


class CooldownConfig(BaseModel):
    seconds: float


@router.post("/cooldown/set")
def set_cooldown(body: CooldownConfig, session: Session = Depends(get_session)):
    """设置代理冷却时间（秒）
    
    建议值：
    - 60-300: 快速轮换（适合大量节点，95个节点约1.5-8小时轮换一次）
    - 300-900: 中等轮换（平衡速度和安全，95个节点约8-24小时轮换一次）
    - 900-7200: 保守轮换（最大安全性，95个节点约24-190小时轮换一次）
    """
    if body.seconds < 0:
        raise HTTPException(400, "冷却时间不能为负数")
    
    proxy_pool.set_cooldown(body.seconds)
    
    # 计算轮换周期
    active_count = session.exec(
        select(ProxyModel).where(ProxyModel.is_active == True)
    ).all()
    total_proxies = len(active_count)
    
    rotation_hours = (body.seconds * total_proxies) / 3600 if total_proxies > 0 else 0
    
    return {
        "cooldown_seconds": body.seconds,
        "cooldown_minutes": round(body.seconds / 60, 2),
        "active_proxies": total_proxies,
        "rotation_cycle_hours": round(rotation_hours, 2),
        "message": f"冷却时间已设置为 {body.seconds} 秒 ({body.seconds/60:.1f} 分钟)"
    }


@router.get("/rotation/stats")
def get_rotation_stats(session: Session = Depends(get_session)):
    """获取代理轮换统计信息"""
    proxies = session.exec(select(ProxyModel)).all()
    active_proxies = [p for p in proxies if p.is_active]
    
    total_success = sum(p.success_count for p in proxies)
    total_fail = sum(p.fail_count for p in proxies)
    total_attempts = total_success + total_fail
    
    success_rate = (total_success / total_attempts * 100) if total_attempts > 0 else 0
    
    # 按成功率排序
    sorted_proxies = sorted(
        active_proxies,
        key=lambda p: p.success_count / max(p.success_count + p.fail_count, 1),
        reverse=True
    )
    
    top_performers = [
        {
            "url": p.url,
            "success_rate": round(p.success_count / max(p.success_count + p.fail_count, 1) * 100, 2),
            "success_count": p.success_count,
            "fail_count": p.fail_count
        }
        for p in sorted_proxies[:10]
    ]
    
    cooldown_status = proxy_pool.get_cooldown_status()
    
    return {
        "total_proxies": len(proxies),
        "active_proxies": len(active_proxies),
        "inactive_proxies": len(proxies) - len(active_proxies),
        "total_success": total_success,
        "total_fail": total_fail,
        "overall_success_rate": round(success_rate, 2),
        "cooldown_seconds": proxy_pool._cooldown_seconds,
        "proxies_cooling_down": sum(1 for s in cooldown_status.values() if s["cooling_down"]),
        "top_performers": top_performers,
        "rotation_cycle_hours": round((proxy_pool._cooldown_seconds * len(active_proxies)) / 3600, 2) if active_proxies else 0
    }


@router.get("/smart/status")
def get_smart_selector_status():
    """获取智能代理选择器状态"""
    blacklist = smart_selector.get_blacklist_status()
    
    return {
        "enabled": True,
        "blacklist_count": len(blacklist),
        "blacklist": blacklist,
        "last_port": smart_selector._last_port,
        "features": {
            "avoid_adjacent_ports": True,
            "randomize_selection": True,
            "auto_blacklist": True,
            "min_success_rate_filter": True
        }
    }


class BlacklistAction(BaseModel):
    url: str
    duration: Optional[float] = 3600  # 默认 1 小时


@router.post("/smart/blacklist/add")
def add_to_blacklist(body: BlacklistAction):
    """手动将代理加入黑名单"""
    smart_selector.add_to_blacklist(body.url, body.duration)
    return {
        "message": f"代理 {body.url} 已加入黑名单",
        "duration_seconds": body.duration,
        "duration_minutes": round(body.duration / 60, 2)
    }


@router.post("/smart/blacklist/clear")
def clear_blacklist():
    """清空黑名单"""
    count = len(smart_selector._blacklist)
    smart_selector._blacklist.clear()
    return {
        "message": f"已清空 {count} 个黑名单代理",
        "cleared": count
    }
