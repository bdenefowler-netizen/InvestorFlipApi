import React, { useEffect, useState } from "react";
import { adminRequestHeaders } from "@/src/lib/admin";

const API_BASE = process.env.REACT_APP_BACKEND_URL || "";

const DEFAULT_SERENITY = {
  name: "Serenity",
  subtitle: "Protector. Shadow. Goofy girl. Forever on the Naughty List.",
  years: "2015 - 2026",
  dedication:
    "Serenity was my goofy girl, my protector, and my shadow. She made me laugh all the time, even when she was just being herself. I will miss her snoring, her presence, and the way she made home feel guarded and full.",
  quote: "Gone, but this time, never forgotten.",
  tags: ["Goofy girl", "Protector", "My shadow", "Naughty List forever"],
};

function SerenityCard({ memory }) {
  return (
    <div className="rounded-2xl border border-rose-200 bg-white/80 p-4 shadow-sm backdrop-blur">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="text-lg font-semibold text-slate-900">{memory.title}</h3>
          {memory.date && <p className="text-xs text-slate-500">{memory.date}</p>}
        </div>
        <span className="rounded-full bg-rose-100 px-3 py-1 text-xs font-medium text-rose-700">
          Serenity
        </span>
      </div>
      <p className="mt-3 whitespace-pre-wrap text-sm leading-6 text-slate-700">{memory.body}</p>
    </div>
  );
}

export default function SerenityArea() {
  const [profile, setProfile] = useState(DEFAULT_SERENITY);
  const [memories, setMemories] = useState([]);
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    async function loadSerenity() {
      try {
        const res = await fetch(`${API_BASE}/api/serenity`);
        if (!res.ok) return;
        const data = await res.json();
        setProfile(data.profile || DEFAULT_SERENITY);
        setMemories(data.memories || []);
      } catch (err) {
        // Keep the page beautiful even before the backend route is wired in.
        setProfile(DEFAULT_SERENITY);
      }
    }
    loadSerenity();
  }, []);

  async function saveMemory(e) {
    e.preventDefault();
    if (!title.trim() || !body.trim()) return;

    const newMemory = {
      title: title.trim(),
      body: body.trim(),
      date: new Date().toLocaleDateString(),
    };

    setSaving(true);
    setMemories((current) => [newMemory, ...current]);
    setTitle("");
    setBody("");

    try {
      const headers = await adminRequestHeaders({ "Content-Type": "application/json" });
      await fetch(`${API_BASE}/api/serenity/memories`, {
        method: "POST",
        headers,
        body: JSON.stringify(newMemory),
      });
    } catch (err) {
      // Local optimistic memory stays visible if backend is not connected yet.
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="min-h-screen bg-gradient-to-br from-slate-950 via-purple-950 to-rose-950 px-4 py-8 text-white">
      <div className="mx-auto max-w-6xl">
        <div className="overflow-hidden rounded-3xl border border-white/15 bg-white/10 shadow-2xl backdrop-blur">
          <div className="grid gap-0 lg:grid-cols-[1.1fr_0.9fr]">
            <div className="p-6 sm:p-10">
              <div className="mb-6 inline-flex rounded-full border border-rose-300/40 bg-rose-300/10 px-4 py-2 text-sm text-rose-100">
                The Serenity Area
              </div>

              <h1 className="text-4xl font-black tracking-tight sm:text-6xl">
                {profile.name}
              </h1>
              <p className="mt-3 text-lg text-rose-100">{profile.subtitle}</p>
              <p className="mt-1 text-sm text-slate-300">{profile.years}</p>

              <div className="mt-8 rounded-2xl border border-white/10 bg-black/20 p-5">
                <p className="text-base leading-8 text-slate-100">{profile.dedication}</p>
                <p className="mt-5 text-xl font-semibold text-rose-100">“{profile.quote}”</p>
              </div>

              <div className="mt-6 flex flex-wrap gap-2">
                {(profile.tags || []).map((tag) => (
                  <span
                    key={tag}
                    className="rounded-full bg-white/10 px-3 py-1 text-sm text-slate-100 ring-1 ring-white/10"
                  >
                    {tag}
                  </span>
                ))}
              </div>
            </div>

            <div className="flex items-center justify-center bg-black/20 p-6 sm:p-10">
              <div className="relative flex h-72 w-72 items-center justify-center rounded-full border border-rose-200/30 bg-gradient-to-br from-rose-200/20 to-purple-300/10 shadow-2xl sm:h-96 sm:w-96">
                <div className="absolute inset-6 rounded-full border border-white/10" />
                <div className="text-center">
                  <div className="text-7xl">🐾</div>
                  <p className="mt-4 text-2xl font-black">Serenity</p>
                  <p className="mt-1 text-sm text-rose-100">Forever home. Forever loved.</p>
                </div>
              </div>
            </div>
          </div>
        </div>

        <div className="mt-8 grid gap-6 lg:grid-cols-[0.9fr_1.1fr]">
          <form onSubmit={saveMemory} className="rounded-3xl border border-white/15 bg-white/10 p-6 backdrop-blur">
            <h2 className="text-2xl font-bold">Add a Serenity memory</h2>
            <p className="mt-2 text-sm text-slate-300">
              Save the funny things, the little moments, the snoring, the protector stories, and every Naughty List memory.
            </p>

            <label className="mt-5 block text-sm font-medium text-slate-200">Memory title</label>
            <input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="Example: Her snoring"
              className="mt-2 w-full rounded-xl border border-white/10 bg-white/90 px-4 py-3 text-slate-900 outline-none focus:ring-2 focus:ring-rose-300"
            />

            <label className="mt-4 block text-sm font-medium text-slate-200">Memory</label>
            <textarea
              value={body}
              onChange={(e) => setBody(e.target.value)}
              placeholder="Write whatever you want to remember..."
              rows={6}
              className="mt-2 w-full rounded-xl border border-white/10 bg-white/90 px-4 py-3 text-slate-900 outline-none focus:ring-2 focus:ring-rose-300"
            />

            <button
              type="submit"
              disabled={saving || !title.trim() || !body.trim()}
              className="mt-5 w-full rounded-xl bg-rose-300 px-5 py-3 font-bold text-slate-950 shadow-lg transition hover:bg-rose-200 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {saving ? "Saving..." : "Save to Serenity's area"}
            </button>
          </form>

          <div className="rounded-3xl border border-white/15 bg-white/10 p-6 backdrop-blur">
            <h2 className="text-2xl font-bold">Serenity memories</h2>
            <div className="mt-5 space-y-4">
              {memories.length === 0 ? (
                <div className="rounded-2xl border border-dashed border-white/20 p-8 text-center text-slate-300">
                  No saved memories yet. Add the first one when you are ready.
                </div>
              ) : (
                memories.map((memory, index) => <SerenityCard key={`${memory.title}-${index}`} memory={memory} />)
              )}
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
