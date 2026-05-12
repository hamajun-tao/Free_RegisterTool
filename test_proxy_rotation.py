#!/usr/bin/env python3
"""测试代理轮换功能"""

import requests
import time
from typing import List, Dict


BASE_URL = "http://localhost:8000"


def test_proxy_stats():
    """测试代理统计信息"""
    print("=" * 60)
    print("测试 1: 代理统计信息")
    print("=" * 60)
    
    response = requests.get(f"{BASE_URL}/api/proxies/rotation/stats")
    if response.status_code == 200:
        data = response.json()
        print(f"✓ 总代理数: {data['total_proxies']}")
        print(f"✓ 激活代理数: {data['active_proxies']}")
        print(f"✓ 整体成功率: {data['overall_success_rate']}%")
        print(f"✓ 冷却时间: {data['cooldown_seconds']} 秒")
        print(f"✓ 轮换周期: {data['rotation_cycle_hours']} 小时")
        print(f"✓ 冷却中代理: {data['proxies_cooling_down']}")
        
        if data['top_performers']:
            print("\n前 5 名高质量代理:")
            for i, p in enumerate(data['top_performers'][:5], 1):
                print(f"  {i}. {p['url']} - 成功率: {p['success_rate']}% "
                      f"(成功: {p['success_count']}, 失败: {p['fail_count']})")
        return True
    else:
        print(f"✗ 请求失败: {response.status_code}")
        return False


def test_cooldown_status():
    """测试冷却状态"""
    print("\n" + "=" * 60)
    print("测试 2: 冷却状态")
    print("=" * 60)
    
    response = requests.get(f"{BASE_URL}/api/proxies/cooldown/status")
    if response.status_code == 200:
        data = response.json()
        print(f"✓ 冷却时间: {data['cooldown_seconds']} 秒")
        print(f"✓ 总代理数: {data['total']}")
        print(f"✓ 冷却中: {data['cooling_down']}")
        
        # 显示前 5 个冷却中的代理
        cooling = [(url, info) for url, info in data['proxies'].items() if info['cooling_down']]
        if cooling:
            print(f"\n前 5 个冷却中的代理:")
            for url, info in cooling[:5]:
                print(f"  {url} - 剩余: {info['remaining_seconds']} 秒")
        else:
            print("\n当前没有代理在冷却中")
        return True
    else:
        print(f"✗ 请求失败: {response.status_code}")
        return False


def test_smart_selector():
    """测试智能选择器"""
    print("\n" + "=" * 60)
    print("测试 3: 智能选择器状态")
    print("=" * 60)
    
    response = requests.get(f"{BASE_URL}/api/proxies/smart/status")
    if response.status_code == 200:
        data = response.json()
        print(f"✓ 智能选择器: {'启用' if data['enabled'] else '禁用'}")
        print(f"✓ 黑名单代理数: {data['blacklist_count']}")
        print(f"✓ 上次使用端口: {data['last_port'] or '无'}")
        
        print("\n功能特性:")
        for feature, enabled in data['features'].items():
            status = "✓" if enabled else "✗"
            print(f"  {status} {feature}")
        
        if data['blacklist']:
            print(f"\n黑名单代理 (前 5 个):")
            for i, (url, info) in enumerate(list(data['blacklist'].items())[:5], 1):
                print(f"  {i}. {url} - 剩余: {info['remaining_seconds']} 秒")
        return True
    else:
        print(f"✗ 请求失败: {response.status_code}")
        return False


def test_set_cooldown(seconds: int = 300):
    """测试设置冷却时间"""
    print("\n" + "=" * 60)
    print(f"测试 4: 设置冷却时间为 {seconds} 秒")
    print("=" * 60)
    
    response = requests.post(
        f"{BASE_URL}/api/proxies/cooldown/set",
        json={"seconds": seconds}
    )
    
    if response.status_code == 200:
        data = response.json()
        print(f"✓ {data['message']}")
        print(f"✓ 冷却时间: {data['cooldown_seconds']} 秒 ({data['cooldown_minutes']} 分钟)")
        print(f"✓ 激活代理数: {data['active_proxies']}")
        print(f"✓ 轮换周期: {data['rotation_cycle_hours']} 小时")
        return True
    else:
        print(f"✗ 请求失败: {response.status_code}")
        return False


def test_proxy_selection_pattern():
    """测试代理选择模式（模拟多次选择）"""
    print("\n" + "=" * 60)
    print("测试 5: 代理选择模式分析")
    print("=" * 60)
    
    print("注意: 此测试需要实际注册任务来触发代理选择")
    print("建议通过前端界面创建测试任务来观察代理轮换效果")
    
    # 获取当前统计
    response = requests.get(f"{BASE_URL}/api/proxies/rotation/stats")
    if response.status_code == 200:
        data = response.json()
        print(f"\n当前状态:")
        print(f"  - 总使用次数: {data['total_success'] + data['total_fail']}")
        print(f"  - 成功次数: {data['total_success']}")
        print(f"  - 失败次数: {data['total_fail']}")
        print(f"  - 成功率: {data['overall_success_rate']}%")
        return True
    return False


def run_all_tests():
    """运行所有测试"""
    print("\n" + "=" * 60)
    print("代理轮换功能测试套件")
    print("=" * 60)
    print(f"后端地址: {BASE_URL}")
    print("=" * 60)
    
    tests = [
        ("代理统计信息", test_proxy_stats),
        ("冷却状态", test_cooldown_status),
        ("智能选择器", test_smart_selector),
        ("设置冷却时间", lambda: test_set_cooldown(300)),
        ("选择模式分析", test_proxy_selection_pattern),
    ]
    
    results = []
    for name, test_func in tests:
        try:
            result = test_func()
            results.append((name, result))
        except Exception as e:
            print(f"\n✗ 测试失败: {e}")
            results.append((name, False))
        time.sleep(0.5)
    
    # 总结
    print("\n" + "=" * 60)
    print("测试总结")
    print("=" * 60)
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for name, result in results:
        status = "✓ 通过" if result else "✗ 失败"
        print(f"{status}: {name}")
    
    print(f"\n总计: {passed}/{total} 测试通过")
    
    if passed == total:
        print("\n🎉 所有测试通过！代理轮换功能正常运行。")
    else:
        print(f"\n⚠️  {total - passed} 个测试失败，请检查后端服务。")
    
    print("\n" + "=" * 60)
    print("使用建议:")
    print("1. 通过前端创建注册任务来实际测试代理轮换")
    print("2. 观察 /api/proxies/rotation/stats 中的成功率变化")
    print("3. 根据实际情况调整冷却时间")
    print("4. 查看 PROXY_ROTATION_GUIDE.md 了解详细配置")
    print("=" * 60)


if __name__ == "__main__":
    try:
        run_all_tests()
    except KeyboardInterrupt:
        print("\n\n测试被用户中断")
    except Exception as e:
        print(f"\n\n测试过程中发生错误: {e}")
