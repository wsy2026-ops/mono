const fs = require('fs');
const mainJs = fs.readFileSync('./src/main.js', 'utf8');

console.log('=== 检查Settings UI的JSX代码 ===\n');

// 查找settings case块
const settingsStart = mainJs.indexOf("case'settings':");
const settingsEnd = mainJs.indexOf('case', settingsStart + 10);

if (settingsStart === -1) {
    console.log('❌ 找不到settings case块');
    process.exit(1);
}

const settingsCode = mainJs.substring(settingsStart, settingsEnd > 0 ? settingsEnd : settingsStart + 3000);

console.log('1. 检查JSX标签匹配...');
const openDivs = (settingsCode.match(/<div/g) || []).length;
const closeDivs = (settingsCode.match(/<\/div>/g) || []).length;
console.log(`   div标签: 开${openDivs} 闭${closeDivs} ${openDivs === closeDivs ? '✓' : '✗'}`);

const openSpans = (settingsCode.match(/<span/g) || []).length;
const closeSpans = (settingsCode.match(/<\/span>/g) || []).length;
console.log(`   span标签: 开${openSpans} 闭${closeSpans} ${openSpans === closeSpans ? '✓' : '✗'}`);

console.log('\n2. 检查关键函数调用...');
const checks = [
    'this.context.listAllSaves()',
    'this.context.getCurrentSlotNumber()',
    'this.context.loadFromLocalSlot',
    'this.context.deleteSaveSlot',
    'this.context.renameSaveSlot',
    'this.context.saveToLocalSlot',
    'formatDate',
    'saves.map'
];

checks.forEach(check => {
    if (settingsCode.includes(check)) {
        console.log(`   ✓ ${check}`);
    } else {
        console.log(`   ✗ ${check} 缺失`);
    }
});

console.log('\n3. 检查可能的语法错误...');

// 检查是否有未闭合的括号在JSX中
const lines = settingsCode.split('\n');
let errors = [];

lines.forEach((line, idx) => {
    // 检查常见错误
    if (line.includes('function(') && line.includes('{') && !line.includes('}')) {
        if (!lines[idx + 1] || !lines[idx + 1].includes('}')) {
            errors.push(`Line ${idx}: 可能有未闭合的函数`);
        }
    }
});

if (errors.length > 0) {
    console.log('   发现潜在问题:');
    errors.forEach(e => console.log('   ' + e));
} else {
    console.log('   ✓ 未发现明显语法错误');
}

console.log('\n4. 统计代码量...');
console.log(`   Settings UI代码行数: ${lines.length}`);
console.log(`   bind(this)调用: ${(settingsCode.match(/\.bind\(this\)/g) || []).length}`);

console.log('\n=== 检查完成 ===');
