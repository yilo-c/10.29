// argv 回显探针：把最后一个参数原样 JSON 输出（保换行），用于验证 spawn 路径不截断多行参数
const args = process.argv.slice(2);
if (args.length) process.stdout.write(JSON.stringify(args[args.length - 1]));
