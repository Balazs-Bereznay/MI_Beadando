import { NextRequest } from 'next/server';
export const dynamic = 'force-dynamic';
export async function GET(request: NextRequest, { params }: { params: Promise<{path:string[]}> }) {
 const {path}=await params;
 const base=process.env.API_INTERNAL_URL || 'http://api:8000';
 const url=new URL('/api/v1/'+path.map(encodeURIComponent).join('/'),base);
 url.search=request.nextUrl.search;
 try {const response=await fetch(url,{cache:'no-store',signal:AbortSignal.timeout(20000),headers:{Accept:'application/json'}}); return new Response(await response.text(),{status:response.status,headers:{'Content-Type':'application/json','Cache-Control':'no-store'}});}
 catch {return Response.json({detail:'The data service is temporarily unavailable. Please try again shortly.'},{status:503});}
}
