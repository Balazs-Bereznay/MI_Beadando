import { Suspense } from 'react';
import Observatory from '@/components/observatory';
export default function Page(){return <Suspense fallback={<p className="boot">Opening methodology…</p>}><Observatory view="about" /></Suspense>}
