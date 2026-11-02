// 浏览器控制台测试脚本
// 复制这段代码到浏览器的开发者工具控制台中运行

console.log('%c=== 游戏功能测试开始 ===', 'color: #74AB6A; font-size: 16px; font-weight: bold');

let testResults = {
    pass: 0,
    fail: 0,
    tests: []
};

function test(name, fn) {
    try {
        fn();
        console.log('%c✓ ' + name, 'color: #74AB6A');
        testResults.pass++;
        testResults.tests.push({ name, result: 'pass' });
        return true;
    } catch(e) {
        console.log('%c✗ ' + name + ': ' + e.message, 'color: #B25242');
        testResults.fail++;
        testResults.tests.push({ name, result: 'fail', error: e.message });
        return false;
    }
}

console.log('\n%c【1】localStorage 可用性检查', 'color: #62BBBE; font-size: 14px');
test('localStorage 可用', () => {
    if (typeof(Storage) === "undefined") {
        throw new Error('localStorage 不可用');
    }
});

test('localStorage 读写测试', () => {
    localStorage.setItem('test_key', 'test_value');
    const value = localStorage.getItem('test_key');
    localStorage.removeItem('test_key');
    if (value !== 'test_value') {
        throw new Error('读写失败');
    }
});

console.log('\n%c【2】存档数据结构测试', 'color: #62BBBE; font-size: 14px');
test('创建测试存档', () => {
    const testSave = {
        gameState: {
            time: { day: 10, hour: 6 },
            playerState: { hp: { amount: 100 } },
            generation: 1
        },
        metadata: {
            name: '浏览器测试存档',
            timestamp: Date.now(),
            day: 10,
            generation: 1
        }
    };
    localStorage.setItem('kubition_save_slot_9', JSON.stringify(testSave));
});

test('读取测试存档', () => {
    const loaded = JSON.parse(localStorage.getItem('kubition_save_slot_9'));
    if (loaded.metadata.name !== '浏览器测试存档') {
        throw new Error('存档名称不匹配');
    }
    if (loaded.gameState.time.day !== 10) {
        throw new Error('游戏数据不匹配');
    }
});

test('重命名测试', () => {
    const save = JSON.parse(localStorage.getItem('kubition_save_slot_9'));
    save.metadata.name = '已重命名的存档';
    localStorage.setItem('kubition_save_slot_9', JSON.stringify(save));

    const renamed = JSON.parse(localStorage.getItem('kubition_save_slot_9'));
    if (renamed.metadata.name !== '已重命名的存档') {
        throw new Error('重命名失败');
    }
});

test('删除测试存档', () => {
    localStorage.removeItem('kubition_save_slot_9');
    if (localStorage.getItem('kubition_save_slot_9') !== null) {
        throw new Error('删除失败');
    }
});

console.log('\n%c【3】多槽位管理测试', 'color: #62BBBE; font-size: 14px');
test('创建10个槽位', () => {
    for (let i = 1; i <= 10; i++) {
        const save = {
            gameState: { slotId: i },
            metadata: { name: 'Slot ' + i, timestamp: Date.now(), day: i * 5, generation: i }
        };
        localStorage.setItem('kubition_save_slot_' + i, JSON.stringify(save));
    }

    // 验证所有槽位
    for (let i = 1; i <= 10; i++) {
        const data = JSON.parse(localStorage.getItem('kubition_save_slot_' + i));
        if (data.gameState.slotId !== i) {
            throw new Error('槽位 ' + i + ' 数据错误');
        }
    }
});

test('槽位独立性验证', () => {
    const slot1 = JSON.parse(localStorage.getItem('kubition_save_slot_1'));
    const slot5 = JSON.parse(localStorage.getItem('kubition_save_slot_5'));

    if (slot1.gameState.slotId === slot5.gameState.slotId) {
        throw new Error('槽位数据混淆');
    }
});

test('当前槽位设置', () => {
    localStorage.setItem('kubition_current_slot', '7');
    const current = parseInt(localStorage.getItem('kubition_current_slot'));
    if (current !== 7) {
        throw new Error('当前槽位设置失败');
    }
});

console.log('\n%c【4】存储容量测试', 'color: #62BBBE; font-size: 14px');
test('计算存储使用', () => {
    let totalSize = 0;
    for (let i = 1; i <= 10; i++) {
        const data = localStorage.getItem('kubition_save_slot_' + i);
        if (data) {
            totalSize += data.length;
        }
    }
    console.log('   当前使用: ' + (totalSize / 1024).toFixed(2) + ' KB');

    if (totalSize > 5 * 1024 * 1024) {
        throw new Error('存储使用超过5MB');
    }
});

console.log('\n%c【5】错误处理测试', 'color: #62BBBE; font-size: 14px');
test('损坏数据处理', () => {
    localStorage.setItem('kubition_save_slot_10', '{invalid json}');
    try {
        JSON.parse(localStorage.getItem('kubition_save_slot_10'));
        throw new Error('应该抛出错误');
    } catch(e) {
        if (e.message === '应该抛出错误') {
            throw e;
        }
        // 正确捕获了JSON解析错误
    }
});

console.log('\n%c【6】清理测试数据', 'color: #62BBBE; font-size: 14px');
test('清空所有测试存档', () => {
    for (let i = 1; i <= 10; i++) {
        localStorage.removeItem('kubition_save_slot_' + i);
    }
    localStorage.removeItem('kubition_current_slot');

    // 验证已清空
    for (let i = 1; i <= 10; i++) {
        if (localStorage.getItem('kubition_save_slot_' + i) !== null) {
            throw new Error('清理不完整');
        }
    }
});

console.log('\n%c╔════════════════════════════════════════╗', 'color: #74AB6A; font-size: 14px');
console.log('%c║          测试结果汇总                  ║', 'color: #74AB6A; font-size: 14px');
console.log('%c╚════════════════════════════════════════╝', 'color: #74AB6A; font-size: 14px');
console.log('\n%c✓ 通过: ' + testResults.pass + ' 项', 'color: #74AB6A; font-size: 14px');
console.log('%c✗ 失败: ' + testResults.fail + ' 项', 'color: #B25242; font-size: 14px');
console.log('%c━ 总计: ' + (testResults.pass + testResults.fail) + ' 项\n', 'color: #62BBBE; font-size: 14px');

if (testResults.fail === 0) {
    console.log('%c🎉 所有浏览器测试通过！', 'color: #74AB6A; font-size: 16px; font-weight: bold');
    console.log('\n%c下一步:', 'color: #CBCB7D; font-size: 14px');
    console.log('%c1. 打开游戏并进入"菜单 → 设置"', 'color: #eee');
    console.log('%c2. 测试保存功能', 'color: #eee');
    console.log('%c3. 刷新页面验证自动加载', 'color: #eee');
    console.log('%c4. 测试多槽位功能', 'color: #eee');
} else {
    console.log('%c⚠️ 部分测试失败', 'color: #B25242; font-size: 16px; font-weight: bold');
    console.table(testResults.tests);
}

console.log('\n%c=== 测试完成 ===', 'color: #74AB6A; font-size: 16px; font-weight: bold');
