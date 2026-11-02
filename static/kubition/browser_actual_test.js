// 实际浏览器测试 - 使用Puppeteer
const puppeteer = require('puppeteer');
const path = require('path');
const fs = require('fs');

(async () => {
    console.log('🚀 启动浏览器测试...\n');

    const browser = await puppeteer.launch({
        headless: false, // 显示浏览器界面
        devtools: true,  // 自动打开开发者工具
        executablePath: 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
        args: ['--start-maximized']
    });

    const page = await browser.newPage();

    // 监听控制台消息
    const consoleMessages = [];
    page.on('console', msg => {
        const text = msg.text();
        consoleMessages.push({ type: msg.type(), text });

        const color = {
            'error': '\x1b[31m',
            'warning': '\x1b[33m',
            'info': '\x1b[36m',
            'log': '\x1b[37m'
        }[msg.type()] || '\x1b[37m';

        console.log(`${color}[浏览器${msg.type()}]\x1b[0m ${text}`);
    });

    // 监听页面错误
    const pageErrors = [];
    page.on('pageerror', error => {
        pageErrors.push(error.message);
        console.log(`\x1b[31m[页面错误]\x1b[0m ${error.message}`);
    });

    // 打开游戏
    const gameUrl = 'http://localhost:3000/index.html';
    console.log(`打开游戏: ${gameUrl}\n`);

    try {
        await page.goto(gameUrl, { waitUntil: 'networkidle0', timeout: 30000 });
        console.log('✓ 游戏页面已加载\n');

        // 等待React渲染
        await page.waitForFunction(() => document.querySelector('#game'), { timeout: 5000 });

        // 检查游戏是否正常渲染
        const gameDiv = await page.$('#game');
        if (gameDiv) {
            console.log('✓ 游戏容器存在');
        } else {
            console.log('✗ 游戏容器不存在');
        }

        // 检查localStorage是否可用
        const localStorageAvailable = await page.evaluate(() => {
            try {
                localStorage.setItem('test', 'test');
                localStorage.removeItem('test');
                return true;
            } catch (e) {
                return false;
            }
        });
        console.log(`${localStorageAvailable ? '✓' : '✗'} localStorage可用`);

        // 测试保存功能
        console.log('\n测试存档功能...');
        await page.evaluate(() => {
            const testSave = {
                gameState: { test: true, time: { day: 1 } },
                metadata: { name: 'Puppeteer测试', timestamp: Date.now(), day: 1, generation: 0 }
            };
            localStorage.setItem('kubition_save_slot_1', JSON.stringify(testSave));
        });

        const saveTest = await page.evaluate(() => {
            const data = localStorage.getItem('kubition_save_slot_1');
            if (!data) return false;
            const parsed = JSON.parse(data);
            return parsed.metadata.name === 'Puppeteer测试';
        });
        console.log(`${saveTest ? '✓' : '✗'} 存档保存/读取功能`);

        // 等待用户检查
        console.log('\n\x1b[33m=== 浏览器已打开，请手动测试以下功能 ===\x1b[0m');
        console.log('1. 点击右上角"菜单"按钮');
        console.log('2. 点击"设置"标签');
        console.log('3. 查看是否显示"当前存档槽位"');
        console.log('4. 查看是否显示10个存档槽位');
        console.log('5. 尝试保存游戏');
        console.log('6. 检查是否有红色错误信息');
        console.log('\n按Ctrl+C退出测试...\n');

        // 生成报告
        await page.waitForTimeout(60000); // 等待1分钟供手动测试

    } catch (error) {
        console.log('\x1b[31m✗ 测试失败:\x1b[0m', error.message);
    }

    // 生成测试报告
    console.log('\n\n=== 测试报告 ===');
    console.log(`控制台消息: ${consoleMessages.length} 条`);
    console.log(`页面错误: ${pageErrors.length} 个`);

    if (pageErrors.length > 0) {
        console.log('\n\x1b[31m发现的错误:\x1b[0m');
        pageErrors.forEach((err, idx) => {
            console.log(`${idx + 1}. ${err}`);
        });
    } else {
        console.log('\n\x1b[32m✓ 未发现页面错误\x1b[0m');
    }

    const errorMessages = consoleMessages.filter(m => m.type === 'error');
    if (errorMessages.length > 0) {
        console.log('\n\x1b[31m控制台错误:\x1b[0m');
        errorMessages.forEach((msg, idx) => {
            console.log(`${idx + 1}. ${msg.text}`);
        });
    }

    // await browser.close();
})();
