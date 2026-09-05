"use client";

import React, { useState, useEffect } from 'react';
import { Users, AlertCircle, ScanLine, Radar, Radio } from 'lucide-react';
import { XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceLine, AreaChart, Area } from 'recharts';

export default function Dashboard() {
  const [cirData, setCirData] = useState<Array<{ delayBin: number; power: number }>>([]);
  const [scanAngle, setScanAngle] = useState(0);
  const [presence, setPresence] = useState({ empty: 1, inside: 0, outside: 0, label: 'empty', confidence: 1 });
  const [metrics, setMetrics] = useState({
    status: 'DISCONNECTED',
    room: [0, 0],
    inside: 0,
    outside: 0,
    updateId: 0,
    lastUpdated: '--:--:--',
    signalDelta: 0
  });

  // The Pi's host is supplied at build/run time; falls back to the dev default.
  const piHost = process.env.NEXT_PUBLIC_AERIS_PI_HOST || '10.230.42.102';
  const wsUrl = process.env.NEXT_PUBLIC_AERIS_WS_URL ||
    (typeof window !== 'undefined' ? `ws://${window.location.hostname}:8000/ws` : 'ws://localhost:8000/ws');

  useEffect(() => {
    let ws: WebSocket | null = null;
    let reconnectTimer: number | undefined;
    let disposed = false;

    const connect = () => {
      if (disposed) return;
      setMetrics(prev => ({ ...prev, status: 'CONNECTING TO PI' }));
      ws = new WebSocket(wsUrl);

      ws.onopen = () => {
        setMetrics(prev => ({ ...prev, status: 'CONNECTED TO PI' }));
      };

      ws.onmessage = (event) => {
        const data = JSON.parse(event.data);
        if (data.status === 'LIVE_PI_DATA') {
          setCirData(data.cir);
          if (data.presence) setPresence(data.presence);
          setMetrics({
            status: 'LIVE',
            room: data.room_dims,
            inside: data.human_counts[0],
            outside: data.human_counts[1],
            updateId: data.update_id,
            lastUpdated: new Date(data.updated_at * 1000).toLocaleTimeString(),
            signalDelta: data.signal_delta
          });
        } else if (data.status === 'WAITING_FOR_TRIAL') {
          setMetrics(prev => ({ ...prev, status: 'WAITING FOR PI TRIAL' }));
        } else if (data.status === 'PI_CONNECTION_FAILED' || data.error) {
          setMetrics(prev => ({ ...prev, status: 'PI CONNECTION FAILED' }));
        }
      };

      ws.onclose = () => {
        if (disposed) return;
        setMetrics(prev => ({ ...prev, status: 'RECONNECTING' }));
        reconnectTimer = window.setTimeout(connect, 1000);
      };

      ws.onerror = () => ws?.close();
    };

    connect();
    return () => {
      disposed = true;
      if (reconnectTimer !== undefined) window.clearTimeout(reconnectTimer);
      ws?.close();
    };
  }, [wsUrl]);

  // Radar scanning animation
  useEffect(() => {
    const radarInterval = setInterval(() => {
      setScanAngle((prev) => (prev + 5) % 360);
    }, 50);
    return () => clearInterval(radarInterval);
  }, []);

  const roomWidth = Number(metrics.room[0]) || 5;
  const roomDepth = Number(metrics.room[1]) || 5;
  const roomScale = Math.min(380 / roomWidth, 260 / roomDepth);
  const roomMapWidth = Math.round(roomWidth * roomScale);
  const roomMapHeight = Math.round(roomDepth * roomScale);

  return (
    <div className="min-h-screen bg-black text-zinc-100 p-8 font-mono">
      
      {/* Top HUD */}
      <div className="flex justify-between items-center mb-8 border-b border-zinc-800 pb-4">
        <div>
          <h1 className="text-3xl font-bold tracking-widest text-white flex items-center gap-4 uppercase">
            <ScanLine className="text-cyan-500 w-8 h-8" />
            AERIS // Spatial Intelligence
          </h1>
          <p className="text-zinc-500 mt-1 text-sm tracking-widest">THROUGH-WALL RF TELEMETRY SYSTEM v3.0</p>
        </div>
        <div className="flex flex-col items-end gap-2">
          <div className={`flex items-center gap-3 px-4 py-1.5 rounded-sm border text-xs font-bold tracking-widest uppercase ${metrics.status === 'LIVE' ? 'bg-emerald-500/10 text-emerald-500 border-emerald-500/30' : 'bg-red-500/10 text-red-500 border-red-500/30'}`}>
            <div className={`w-2 h-2 rounded-full animate-pulse ${metrics.status === 'LIVE' ? 'bg-emerald-500' : 'bg-red-500'}`}></div>
            {metrics.status}
          </div>
          <span className="text-zinc-600 text-xs">PI {piHost}</span>
          <span className="text-zinc-600 text-xs">FRAME {metrics.updateId} | UPDATED {metrics.lastUpdated} | Δ {metrics.signalDelta.toFixed(3)}</span>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-8 mb-8">
        
        {/* Left Column: Radar & Spatial Mapping */}
        <div className="lg:col-span-4 flex flex-col gap-8">
          
          <div className="bg-[#09090b] border border-zinc-800 rounded-lg p-6 shadow-2xl relative overflow-hidden h-[400px] flex flex-col">
            <div className="flex items-start justify-between mb-3">
              <div>
                <div className="text-xs text-zinc-500 tracking-widest">TOP-DOWN ROOM PLAN</div>
                <div className="text-[11px] text-zinc-700 tracking-widest mt-1">LIVE SPATIAL FIELD</div>
              </div>
              <div className="flex items-center gap-2 text-[10px] text-cyan-500 tracking-widest">
                <span className="w-1.5 h-1.5 rounded-full bg-cyan-400 animate-pulse" />
                RF MAP
              </div>
            </div>

            <div className="relative flex-1 min-h-0 flex items-center justify-center">
              <div className="relative max-w-full border-2 border-zinc-600 bg-[#0b1114] overflow-hidden" style={{ width: roomMapWidth, height: roomMapHeight }}>
                <div className="absolute inset-0 opacity-25" style={{backgroundImage: 'linear-gradient(rgba(161,161,170,0.2) 1px, transparent 1px), linear-gradient(90deg, rgba(161,161,170,0.2) 1px, transparent 1px)', backgroundSize: '24px 24px'}} />
                <div className="absolute inset-3 border border-zinc-700" />


                <div className="absolute left-[12%] right-[12%] bottom-5 border-t border-zinc-600/70 pt-1 text-center text-[8px] text-zinc-500 tracking-widest">
                  WIDTH {roomWidth.toFixed(1)}m
                </div>
                <div className="absolute right-2 top-1/2 -translate-y-1/2 rotate-90 origin-center text-[8px] text-zinc-500 tracking-widest whitespace-nowrap">
                  DEPTH {roomDepth.toFixed(1)}m
                </div>

                {/* Center sensor anchor; no deployment-specific placement is assumed. */}
                <div className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 z-10">
                  <div className="w-5 h-5 rounded-full bg-cyan-400 flex items-center justify-center">
                    <Radio className="w-2.5 h-2.5 text-black" />
                  </div>
                  <span className="absolute left-1/2 -translate-x-1/2 top-6 text-[8px] text-cyan-300 tracking-widest">SENSOR</span>
                </div>

                {/* Generic occupancy marker, independent of a room's furniture or layout. */}
                {metrics.inside > 0 && <div className="absolute left-[34%] top-[50%] -translate-x-1/2 -translate-y-1/2">
                  <div className="absolute inset-[-8px] rounded-full border border-blue-400/25 animate-ping" />
                  <div className="relative w-7 h-7 rounded-full bg-blue-500/15 border border-blue-400 flex items-center justify-center">
                    <Users className="w-4 h-4 text-blue-300" />
                  </div>
                  <span className="absolute left-1/2 -translate-x-1/2 mt-1 whitespace-nowrap text-[8px] text-blue-300 tracking-widest">OCCUPANT</span>
                </div>}

                {metrics.outside > 0 && <div className="absolute right-[5%] top-1/2 -translate-y-1/2">
                  <div className="w-3 h-3 rounded-full bg-orange-500 shadow-[0_0_16px_rgba(249,115,22,0.8)] animate-pulse" />
                  <span className="absolute right-5 top-0 whitespace-nowrap text-[8px] text-orange-300 tracking-widest">OUTSIDE ZONE</span>
                </div>}

                {/* Subtle generic sensing direction, with no room-specific orientation. */}
                <div className="absolute left-1/2 top-1/2 w-px h-[42%] origin-bottom bg-cyan-300/60" style={{transform: `translateY(-100%) rotate(${scanAngle}deg)`}} />
              </div>
            </div>

            <div className="flex items-center justify-between mt-4 text-[10px] tracking-widest text-zinc-600">
              <span className="flex items-center gap-2"><span className="w-2 h-2 rounded-full bg-blue-400" /> OCCUPANT</span>
              <span className="flex items-center gap-2"><span className="w-2 h-2 rounded-full bg-cyan-400" /> SENSOR</span>
              <span>{roomWidth.toFixed(1)}m × {roomDepth.toFixed(1)}m</span>
            </div>
          </div>

          {/* Quick Metrics */}
          <div className="grid grid-cols-2 gap-4">
            <div className="bg-[#09090b] border border-zinc-800 rounded-lg p-4">
               <h3 className="text-zinc-500 text-xs tracking-widest mb-2">ROOM DIMS</h3>
               <div className="text-3xl font-bold text-cyan-400">{metrics.room[0]}<span className="text-zinc-600 text-xl mx-1">x</span>{metrics.room[1]}</div>
               <div className="text-zinc-600 text-xs mt-1">METERS</div>
            </div>
            <div className="bg-[#09090b] border border-zinc-800 rounded-lg p-4">
               <h3 className="text-zinc-500 text-xs tracking-widest mb-2">CONFIDENCE</h3>
               <div className="text-3xl font-bold text-emerald-400">{Math.round(presence.confidence * 100)}%</div>
               <div className="text-zinc-600 text-xs mt-1 uppercase">{presence.label} / OSS MODEL</div>
            </div>
          </div>
        </div>

        {/* Right Column: Advanced Telemetry */}
        <div className="lg:col-span-8 flex flex-col gap-8">
          
          <div className="grid grid-cols-2 gap-8">
            <div className="bg-[#09090b] border border-zinc-800 rounded-lg p-6 relative">
              <h2 className="text-zinc-500 text-xs tracking-widest mb-4">INSIDE OCCUPANCY</h2>
              <div className="text-8xl font-black text-blue-500">{metrics.inside}</div>
              <Users className="absolute bottom-6 right-6 w-16 h-16 text-blue-500/20" />
            </div>
            
            <div className="bg-[#09090b] border border-zinc-800 rounded-lg p-6 relative">
              <h2 className="text-zinc-500 text-xs tracking-widest mb-4 flex items-center justify-between">
                THROUGH-WALL OCCUPANCY
                {metrics.outside > 0 && <span className="text-red-500 animate-pulse flex items-center gap-1"><AlertCircle className="w-4 h-4"/> DETECTED</span>}
              </h2>
              <div className="text-8xl font-black text-orange-500">{metrics.outside}</div>
              <Radar className="absolute bottom-6 right-6 w-16 h-16 text-orange-500/20" />
            </div>
          </div>

          <div className="bg-[#09090b] border border-zinc-800 rounded-lg p-6 flex-1">
            <h2 className="text-zinc-500 text-xs tracking-widest mb-6">RAW MULTIPATH ECHO TELEMETRY (PDP)</h2>
            <div className="h-64 w-full">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={cirData} margin={{ top: 0, right: 0, bottom: 0, left: -20 }}>
                  <defs>
                    <linearGradient id="colorPower" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#06b6d4" stopOpacity={0.3}/>
                      <stop offset="95%" stopColor="#06b6d4" stopOpacity={0}/>
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="1 4" stroke="#27272a" vertical={false} />
                  <XAxis dataKey="delayBin" stroke="#3f3f46" tick={{fill: '#52525b', fontSize: 10}} />
                  <YAxis stroke="#3f3f46" tick={{fill: '#52525b', fontSize: 10}} />
                  <Tooltip 
                    contentStyle={{ backgroundColor: '#09090b', border: '1px solid #27272a', borderRadius: '4px', fontFamily: 'monospace' }}
                    itemStyle={{ color: '#e4e4e7' }}
                  />
                  <ReferenceLine x={15} stroke="#a855f7" strokeDasharray="3 3" label={{ position: 'top', value: 'PHYSICAL BOUNDARY', fill: '#a855f7', fontSize: 10 }} />
                  <Area type="monotone" dataKey="power" stroke="#06b6d4" strokeWidth={2} fillOpacity={1} fill="url(#colorPower)" activeDot={{ r: 6, fill: '#06b6d4' }} />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          </div>

        </div>
      </div>
    </div>
  );
}
