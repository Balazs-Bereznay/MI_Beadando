import { Suspense } from 'react';
import Observatory from '@/components/observatory';
export default async function Page({params}:{params:Promise<{id:string}>}){const {id}=await params;return <Suspense fallback={<p className="boot">Opening topic…</p>}><Observatory view="detail" topicId={id} /></Suspense>}
