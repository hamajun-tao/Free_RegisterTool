"""智能代理选择器 - 基于风控规避的高级代理轮换策略"""

import time
import random
from typing import Optional
from sqlmodel import Session, select
from .db import ProxyModel, engine
from .proxy_pool import proxy_pool


class SmartProxySelector:
    """智能代理选择器
    
    特性：
    1. 避免连续使用相邻端口（降低批量检测风险）
    2. 优先选择成功率高的代理
    3. 自动跳过冷却中的代理
    4. 支持随机化选择（打乱时序特征）
    5. 黑名单机制（临时屏蔽高风险代理）
    """
    
    def __init__(self):
        self._last_port: Optional[int] = None
        self._blacklist: dict[str, float] = {}  # url -> 黑名单到期时间
        self._blacklist_duration = 3600  # 黑名单持续时间（秒）
    
    def _extract_port(self, url: str) -> Optional[int]:
        """从代理 URL 提取端口号"""
        try:
            if ":" in url:
                port_str = url.split(":")[-1].strip("/")
                return int(port_str)
        except:
            pass
        return None
    
    def _is_blacklisted(self, url: str) -> bool:
        """检查代理是否在黑名单中"""
        expire_time = self._blacklist.get(url)
        if expire_time is None:
            return False
        
        if time.time() > expire_time:
            # 黑名单已过期，移除
            del self._blacklist[url]
            return False
        
        return True
    
    def add_to_blacklist(self, url: str, duration: Optional[float] = None):
        """将代理加入黑名单
        
        Args:
            url: 代理 URL
            duration: 黑名单持续时间（秒），None 使用默认值
        """
        duration = duration or self._blacklist_duration
        self._blacklist[url] = time.time() + duration
    
    def get_smart_proxy(
        self,
        region: str = "",
        avoid_adjacent_ports: bool = True,
        randomize: bool = True,
        min_success_rate: float = 0.0
    ) -> Optional[str]:
        """智能选择代理
        
        Args:
            region: 区域过滤
            avoid_adjacent_ports: 是否避免相邻端口
            randomize: 是否随机化选择（从前 N 个高质量代理中随机选）
            min_success_rate: 最低成功率要求（0.0-1.0）
        
        Returns:
            代理 URL，如果没有可用代理则返回 None
        """
        with Session(engine) as session:
            # 获取所有激活的代理
            query = select(ProxyModel).where(ProxyModel.is_active == True)
            if region:
                query = query.where(ProxyModel.region == region)
            
            proxies = session.exec(query).all()
            
            if not proxies:
                return None
            
            # 过滤：移除黑名单代理
            proxies = [p for p in proxies if not self._is_blacklisted(p.url)]
            
            # 过滤：移除冷却中的代理
            cooldown_status = proxy_pool.get_cooldown_status()
            proxies = [
                p for p in proxies
                if not cooldown_status.get(p.url, {}).get("cooling_down", False)
            ]
            
            if not proxies:
                # 如果所有代理都在冷却，返回冷却时间最短的
                all_proxies = session.exec(query).all()
                if not all_proxies:
                    return None
                
                # 按冷却剩余时间排序
                sorted_by_cooldown = sorted(
                    all_proxies,
                    key=lambda p: cooldown_status.get(p.url, {}).get("remaining_seconds", 0)
                )
                selected = sorted_by_cooldown[0]
                proxy_pool.mark_used(selected.url)
                return selected.url
            
            # 过滤：成功率要求
            if min_success_rate > 0:
                proxies = [
                    p for p in proxies
                    if (p.success_count / max(p.success_count + p.fail_count, 1)) >= min_success_rate
                ]
            
            if not proxies:
                # 降低成功率要求重试
                return self.get_smart_proxy(region, avoid_adjacent_ports, randomize, 0.0)
            
            # 按成功率排序
            proxies.sort(
                key=lambda p: p.success_count / max(p.success_count + p.fail_count, 1),
                reverse=True
            )
            
            # 避免相邻端口
            if avoid_adjacent_ports and self._last_port is not None:
                filtered = []
                for p in proxies:
                    port = self._extract_port(p.url)
                    if port is None:
                        filtered.append(p)
                    elif abs(port - self._last_port) > 5:  # 端口间隔至少 5
                        filtered.append(p)
                
                if filtered:
                    proxies = filtered
            
            # 随机化选择（从前 20% 高质量代理中随机选）
            if randomize and len(proxies) > 1:
                top_n = max(1, len(proxies) // 5)  # 前 20%
                candidates = proxies[:top_n]
                selected = random.choice(candidates)
            else:
                selected = proxies[0]
            
            # 记录选择的端口
            port = self._extract_port(selected.url)
            if port is not None:
                self._last_port = port
            
            # 标记为已使用（开始冷却）
            proxy_pool.mark_used(selected.url)
            
            return selected.url
    
    def report_proxy_result(self, url: str, success: bool, auto_blacklist: bool = True):
        """报告代理使用结果
        
        Args:
            url: 代理 URL
            success: 是否成功
            auto_blacklist: 失败时是否自动加入黑名单
        """
        if success:
            proxy_pool.report_success(url)
        else:
            proxy_pool.report_fail(url)
            
            # 连续失败自动加入黑名单
            if auto_blacklist:
                with Session(engine) as session:
                    proxy = session.exec(
                        select(ProxyModel).where(ProxyModel.url == url)
                    ).first()
                    
                    if proxy:
                        # 如果最近 5 次都失败，加入黑名单 1 小时
                        if proxy.fail_count >= 5 and proxy.success_count == 0:
                            self.add_to_blacklist(url, 3600)
                        # 如果成功率低于 20%，加入黑名单 30 分钟
                        elif proxy.fail_count > 0:
                            success_rate = proxy.success_count / (proxy.success_count + proxy.fail_count)
                            if success_rate < 0.2:
                                self.add_to_blacklist(url, 1800)
    
    def get_blacklist_status(self) -> dict:
        """获取黑名单状态"""
        now = time.time()
        active_blacklist = {}
        
        for url, expire_time in list(self._blacklist.items()):
            if expire_time > now:
                remaining = expire_time - now
                active_blacklist[url] = {
                    "remaining_seconds": round(remaining, 1),
                    "expire_time": expire_time
                }
            else:
                del self._blacklist[url]
        
        return active_blacklist


# 全局实例
smart_selector = SmartProxySelector()
