import type { Metadata } from 'next';
import './globals.css';
export const metadata: Metadata = { title: 'Stacklens — Developer question trends', description: 'An observatory for emerging technical questions. Explore topics, trace trends, and inspect the evidence.' };
export default function RootLayout({ children }: { children: React.ReactNode }) { return <html lang="en"><body>{children}</body></html>; }
