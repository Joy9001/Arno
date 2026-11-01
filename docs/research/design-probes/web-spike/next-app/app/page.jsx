"use client";
import dynamic from "next/dynamic";
const ChatApp = dynamic(() => import("../components/ChatApp").then((m) => m.ChatApp), { ssr: false });
export default function Page() { return <ChatApp />; }
