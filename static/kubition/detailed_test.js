// 详细错误检测
const puppeteer = require('puppeteer');

(async () => {
    console.log('🔍 详细错误检测...\n');

    const browser = await puppeteer.launch({
        headless: false,
        executablePath: 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
        devtools: true,
        args: ['--start-maximized']
    });

    const page = await browser.newPage();

    // 捕获所有控制台消息
    page.on('console', msg => {
        const type = msg.type();
        const text = msg.text();

        if (type === 'error') {
            console.log(`❌ ERROR: ${text}`);
        } else if (type === 'warning') {
            console.log(`⚠️  WARNING: ${text}`);
        }
    });

    // 捕获页面错误
    page.on('pageerror', error => {
        console.log(`\n❌ 页面错误:`);
        console.log(error.stack || error.message);
    });

    // 捕获请求失败
    page.on('requestfailed', request => {
        console.log(`❌ 请求失败: ${request.url()}`);
        console.log(`   原因: ${request.failure().errorText}`);
    });

    try {
        console.log('打开游戏页面...\n');
        await page.goto('http://localhost:3000/index.html', {
            waitUntil: 'networkidle2',
            timeout: 30000
        });

        // 等待游戏容器
        await page.waitForFunction(() => document.querySelector('#game'), { timeout: 10000 });

        console.log('\n✅ 游戏页面加载完成');

        // 检查React是否渲染成功
        const hasContent = await page.evaluate(() => {
            const game = document.querySelector('#game');
            return game && game.innerHTML.length > 100;
        });

        if (hasContent) {
            console.log('✅ React组件已渲染');
        } else {
            console.log('❌ React组件未渲染或内容为空');
        }

        // 尝试触发菜单
        console.log('\n测试打开菜单...');
        const menuButton = await page.$('.stateVector');
        if (menuButton) {
            console.log('✅ 找到菜单按钮');
            await menuButton.click();
            await page.waitForFunction(() => {
                const menu = document.querySelector('.menuOuter');
                return menu !== null;
            }, { timeout: 5000 });
            console.log('✅ 菜单已打开');
        } else {
            console.log('❌ 未找到菜单按钮');
        }

        console.log('\n\n=== 测试完成 ===');
        console.log('浏览器将保持打开，请手动检查');
        console.log('- 查看控制台是否有红色错误');
        console.log('- 尝试点击"菜单 → 设置"');
        console.log('- 检查存档管理界面');
        console.log('\n按Ctrl+C退出...');

        // 保持浏览器打开
        await new Promise(() => {});

    } catch (error) {
        console.log(`\n❌ 测试失败: ${error.message}`);
        console.log(error.stack);
    }
})();
