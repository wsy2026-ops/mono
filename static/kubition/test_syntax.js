// 简单的语法检查脚本
const fs = require('fs');
const vm = require('vm');

console.log('检查 main.js 语法...');

try {
    const mainJs = fs.readFileSync('./src/main.js', 'utf8');

    // 移除 JSX 语法进行基本检查（因为这是React代码）
    // 我们主要检查我们添加的函数是否有明显的语法错误

    console.log('✓ main.js 文件读取成功');
    console.log('文件大小:', mainJs.length, '字符');

    // 检查关键函数是否存在
    const functionsToCheck = [
        'isLocalStorageAvailable',
        'getCurrentSlotNumber',
        'listAllSaves',
        'saveToLocalSlot',
        'loadFromLocalSlot',
        'deleteSaveSlot',
        'renameSaveSlot',
        'autoSave',
        'tryLoadLastSave'
    ];

    console.log('\n检查新增函数...');
    functionsToCheck.forEach(funcName => {
        if (mainJs.includes(funcName)) {
            console.log('✓', funcName, '找到');
        } else {
            console.log('✗', funcName, '未找到');
        }
    });

    // 检查可能的语法问题
    console.log('\n检查潜在问题...');

    // 检查未闭合的括号
    const openBraces = (mainJs.match(/{/g) || []).length;
    const closeBraces = (mainJs.match(/}/g) || []).length;
    console.log('大括号匹配:', openBraces === closeBraces ? '✓' : '✗ 不匹配!');
    if (openBraces !== closeBraces) {
        console.log('  开括号:', openBraces, '闭括号:', closeBraces);
    }

    const openParens = (mainJs.match(/\(/g) || []).length;
    const closeParens = (mainJs.match(/\)/g) || []).length;
    console.log('圆括号匹配:', openParens === closeParens ? '✓' : '✗ 不匹配!');
    if (openParens !== closeParens) {
        console.log('  开括号:', openParens, '闭括号:', closeParens);
    }

    // 检查 SAVE_URL 是否被注释
    const dataJs = fs.readFileSync('./src/data.js', 'utf8');
    if (dataJs.includes('// var SAVE_URL')) {
        console.log('✓ SAVE_URL 已被注释');
    } else {
        console.log('⚠ SAVE_URL 状态不明确');
    }

    console.log('\n基本语法检查完成！');

} catch (error) {
    console.error('错误:', error.message);
    process.exit(1);
}
