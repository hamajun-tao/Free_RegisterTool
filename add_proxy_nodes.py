#!/usr/bin/env python3
"""批量添加代理节点到数据库"""

import sys
from sqlmodel import Session, select
from core.db import ProxyModel, engine


def add_proxy_nodes():
    """添加 7900-7994 端口的代理节点"""
    
    # 生成代理列表
    proxy_urls = []
    for port in range(7900, 7995):  # 7900-7994
        proxy_urls.append(f"http://127.0.0.1:{port}")
    
    print(f"准备添加 {len(proxy_urls)} 个代理节点...")
    
    with Session(engine) as session:
        added = 0
        updated = 0
        
        for url in proxy_urls:
            # 检查是否已存在
            existing = session.exec(
                select(ProxyModel).where(ProxyModel.url == url)
            ).first()
            
            if existing:
                # 更新现有代理，确保激活
                existing.is_active = True
                existing.region = "local"
                session.add(existing)
                updated += 1
                print(f"✓ 更新: {url}")
            else:
                # 添加新代理
                proxy = ProxyModel(
                    url=url,
                    region="local",
                    is_active=True,
                    success_count=0,
                    fail_count=0
                )
                session.add(proxy)
                added += 1
                print(f"+ 添加: {url}")
        
        session.commit()
    
    print(f"\n完成！添加 {added} 个新节点，更新 {updated} 个现有节点")
    print(f"总计: {len(proxy_urls)} 个代理节点已就绪")


def set_cooldown_time(seconds: float = 300):
    """设置代理冷却时间（秒）
    
    Args:
        seconds: 冷却时间，默认 300 秒（5分钟）
                建议值：
                - 60-300: 快速轮换（适合大量节点）
                - 300-900: 中等轮换（平衡速度和安全）
                - 900-7200: 保守轮换（最大安全性）
    """
    from core.proxy_pool import proxy_pool
    proxy_pool.set_cooldown(seconds)
    print(f"代理冷却时间已设置为: {seconds} 秒 ({seconds/60:.1f} 分钟)")


if __name__ == "__main__":
    print("=" * 60)
    print("代理节点批量添加工具")
    print("=" * 60)
    
    # 添加代理节点
    add_proxy_nodes()
    
    # 设置冷却时间为 5 分钟（适合 95 个节点的轮换）
    print("\n" + "=" * 60)
    cooldown = 300  # 5 分钟
    set_cooldown_time(cooldown)
    
    print("\n" + "=" * 60)
    print("使用建议:")
    print("1. 95 个节点，5分钟冷却 = 每个节点约 8 小时轮换一次")
    print("2. 可通过 API 动态调整冷却时间")
    print("3. 系统会自动选择成功率高的节点")
    print("4. 失败节点会自动降权或禁用")
    print("=" * 60)
