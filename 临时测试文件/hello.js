"use strict";
/**
 * hello.ts — 一个简单的 Hello World 程序（TypeScript 版）。
 *
 * 使用 TypeScript 的类型标注和现代 ES6+ 语法，
 * 将问候消息打印到标准输出。
 * 运行方式：npx ts-node hello.ts  或  先 tsc 编译再 node hello.js
 */
/** 问候消息常量 */
const GREETING = "Hello, World!";
/**
 * 程序入口函数。向控制台打印问候消息。
 */
const main = () => {
    console.log(GREETING);
};
// 执行入口
main();
