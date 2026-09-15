/**
 * Spaces the caller can reach, and who else can reach the ones they own.
 *
 * Only owners see the member controls — an editor can fill a space with
 * content but cannot widen access to it, and the API enforces that
 * independently.
 */

import { type FormEvent, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, Loader2, Plus, Users } from "lucide-react";
import {
  addMember,
  createSpace,
  fetchMembers,
  fetchMySpaces,
  removeMember,
  updateMemberRole,
  type Space,
  type SpaceMember,
  type SpaceRole,
} from "@/api/spaces";
import { ApiError } from "@/api/client";
import type { Me } from "@/api/me";

const ROLE_BLURB: Record<SpaceRole, string> = {
  viewer: "Can read items, attachments and notes.",
  editor: "Can also add, edit and delete content.",
  owner: "Can also manage who has access.",
};

export default function SpacesSection({ user }: { user: Me }) {
  const spaces = useQuery<Space[], ApiError>({
    queryKey: ["spaces"],
    queryFn: fetchMySpaces,
    retry: (_a, err) => err.status >= 500,
  });

  return (
    <section
      className="mb-6 rounded border p-4"
      style={{
        backgroundColor: "var(--color-surface)",
        borderColor: "var(--color-border)",
      }}
    >
      <h2 className="mb-1 text-sm font-medium">Spaces</h2>
      <p className="mb-3 text-xs" style={{ color: "var(--color-text-muted)" }}>
        Spaces you own, plus any you've been added to. Sharing is per space:
        add someone as a viewer to let them read, or an editor to let them
        contribute.
      </p>

      {spaces.isLoading && (
        <p className="text-sm" style={{ color: "var(--color-text-muted)" }}>
          Loading…
        </p>
      )}
      {spaces.error && (
        <p className="text-sm text-red-600" role="alert">
          Could not load spaces: {spaces.error.message}
        </p>
      )}

      <ul className="flex flex-col gap-1">
        {spaces.data?.map((space) => (
          <SpaceRow key={space.id} space={space} />
        ))}
      </ul>

      {user.is_admin && <NewSpaceForm />}
    </section>
  );
}

/**
 * Admin-only. Spaces are cheap to make and awkward to clean up, so the
 * API gates creation the same way — this hiding is cosmetic.
 */
function NewSpaceForm() {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");

  const create = useMutation({
    mutationFn: () => createSpace(name.trim(), slug.trim() || undefined),
    onSuccess: () => {
      setName("");
      setSlug("");
      setOpen(false);
      qc.invalidateQueries({ queryKey: ["spaces"] });
    },
  });

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="mt-2 flex items-center gap-1 rounded border px-2 py-1 text-xs hover:opacity-80"
        style={{ borderColor: "var(--color-border)" }}
      >
        <Plus className="h-3.5 w-3.5" />
        New space
      </button>
    );
  }

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        if (name.trim() && !create.isPending) create.mutate();
      }}
      className="mt-2 rounded border p-2"
      style={{ borderColor: "var(--color-border)" }}
    >
      <div className="flex flex-wrap gap-1">
        <input
          required
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Space name"
          aria-label="Name for the new space"
          className="min-w-0 flex-1 rounded border px-2 py-1 text-xs"
          style={{
            borderColor: "var(--color-border)",
            backgroundColor: "var(--color-surface)",
          }}
        />
        <input
          value={slug}
          onChange={(e) => setSlug(e.target.value)}
          placeholder="slug (optional)"
          aria-label="Slug for the new space"
          className="min-w-0 flex-1 rounded border px-2 py-1 text-xs"
          style={{
            borderColor: "var(--color-border)",
            backgroundColor: "var(--color-surface)",
          }}
        />
        <button
          type="submit"
          disabled={create.isPending}
          className="flex shrink-0 items-center gap-1 rounded border px-2 py-1 text-xs hover:opacity-80 disabled:opacity-50"
          style={{ borderColor: "var(--color-border)" }}
        >
          {create.isPending && <Loader2 className="h-3 w-3 animate-spin" />}
          Create
        </button>
        <button
          type="button"
          onClick={() => setOpen(false)}
          className="shrink-0 rounded border px-2 py-1 text-xs hover:opacity-80"
          style={{ borderColor: "var(--color-border)" }}
        >
          Cancel
        </button>
      </div>
      <p className="mt-1 text-xs" style={{ color: "var(--color-text-muted)" }}>
        You'll own it, and can add people from the Sharing control once it
        exists. The slug is derived from the name when left blank.
      </p>
      {create.error && (
        <p className="mt-1 text-xs text-red-600" role="alert">
          {create.error instanceof ApiError && create.error.status === 409
            ? "A space with that slug already exists."
            : create.error.message}
        </p>
      )}
    </form>
  );
}

function SpaceRow({ space }: { space: Space }) {
  const [open, setOpen] = useState(false);
  const Chevron = open ? ChevronDown : ChevronRight;

  return (
    <li
      className="rounded border px-3 py-2"
      style={{ borderColor: "var(--color-border)" }}
    >
      <div className="flex items-center gap-2">
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm">
            {space.name}
            {space.is_personal && (
              <span
                className="ml-2 text-xs"
                style={{ color: "var(--color-text-muted)" }}
              >
                personal
              </span>
            )}
          </span>
          <span
            className="block truncate text-xs"
            style={{ color: "var(--color-text-muted)" }}
          >
            {space.slug} · you are {space.role}
          </span>
        </span>
        {space.is_owner && (
          <button
            onClick={() => setOpen((v) => !v)}
            aria-expanded={open}
            className="flex shrink-0 items-center gap-1 rounded border px-2 py-1 text-xs hover:opacity-80"
            style={{ borderColor: "var(--color-border)" }}
          >
            <Chevron className="h-3.5 w-3.5" />
            <Users className="h-3.5 w-3.5" />
            Sharing
          </button>
        )}
      </div>
      {open && space.is_owner && <MemberList slug={space.slug} />}
    </li>
  );
}

function MemberList({ slug }: { slug: string }) {
  const qc = useQueryClient();
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Exclude<SpaceRole, "owner">>("viewer");

  const members = useQuery<SpaceMember[], ApiError>({
    queryKey: ["members", slug],
    queryFn: () => fetchMembers(slug),
    retry: (_a, err) => err.status >= 500,
  });

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["members", slug] });
  };

  const add = useMutation({
    mutationFn: () => addMember(slug, email.trim(), role),
    onSuccess: () => {
      setEmail("");
      invalidate();
    },
  });

  const changeRole = useMutation({
    mutationFn: ({
      userId,
      next,
    }: {
      userId: string;
      next: Exclude<SpaceRole, "owner">;
    }) => updateMemberRole(slug, userId, next),
    onSuccess: invalidate,
  });

  const remove = useMutation({
    mutationFn: (userId: string) => removeMember(slug, userId),
    onSuccess: invalidate,
  });

  function onAdd(e: FormEvent) {
    e.preventDefault();
    if (!email.trim() || add.isPending) return;
    add.mutate();
  }

  const failure = add.error ?? changeRole.error ?? remove.error;

  return (
    <div
      className="mt-2 border-t pt-2"
      style={{ borderColor: "var(--color-border)" }}
    >
      {members.isLoading && (
        <p className="text-xs" style={{ color: "var(--color-text-muted)" }}>
          Loading members…
        </p>
      )}

      <ul className="mb-2 flex flex-col gap-1">
        {members.data?.map((m) => (
          <li key={m.user_id} className="flex items-center gap-2 text-sm">
            <span className="min-w-0 flex-1">
              <span className="block truncate">{m.display_name}</span>
              <span
                className="block truncate text-xs"
                style={{ color: "var(--color-text-muted)" }}
              >
                {m.email}
              </span>
            </span>
            {m.is_owner ? (
              <span
                className="shrink-0 text-xs"
                style={{ color: "var(--color-text-muted)" }}
                title={ROLE_BLURB.owner}
              >
                owner
              </span>
            ) : (
              <>
                <select
                  value={m.role}
                  aria-label={`Role for ${m.email}`}
                  title={ROLE_BLURB[m.role]}
                  disabled={changeRole.isPending}
                  onChange={(e) =>
                    changeRole.mutate({
                      userId: m.user_id,
                      next: e.target.value as Exclude<SpaceRole, "owner">,
                    })
                  }
                  className="shrink-0 rounded border px-1.5 py-0.5 text-xs disabled:opacity-50"
                  style={{
                    borderColor: "var(--color-border)",
                    backgroundColor: "var(--color-surface)",
                  }}
                >
                  <option value="viewer">viewer</option>
                  <option value="editor">editor</option>
                </select>
                <button
                  onClick={() => remove.mutate(m.user_id)}
                  disabled={remove.isPending}
                  aria-label={`Remove ${m.email}`}
                  className="shrink-0 rounded border px-2 py-0.5 text-xs hover:opacity-80 disabled:opacity-50"
                  style={{ borderColor: "var(--color-border)" }}
                >
                  Remove
                </button>
              </>
            )}
          </li>
        ))}
      </ul>

      <form onSubmit={onAdd} className="flex flex-wrap gap-1">
        <input
          type="email"
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="Add by email…"
          aria-label="Email of the person to add"
          className="min-w-0 flex-1 rounded border px-2 py-1 text-xs"
          style={{
            borderColor: "var(--color-border)",
            backgroundColor: "var(--color-surface)",
          }}
        />
        <select
          value={role}
          aria-label="Role for the new member"
          onChange={(e) =>
            setRole(e.target.value as Exclude<SpaceRole, "owner">)
          }
          className="shrink-0 rounded border px-1.5 py-1 text-xs"
          style={{
            borderColor: "var(--color-border)",
            backgroundColor: "var(--color-surface)",
          }}
        >
          <option value="viewer">viewer</option>
          <option value="editor">editor</option>
        </select>
        <button
          type="submit"
          disabled={add.isPending}
          className="flex shrink-0 items-center gap-1 rounded border px-2 py-1 text-xs hover:opacity-80 disabled:opacity-50"
          style={{ borderColor: "var(--color-border)" }}
        >
          {add.isPending && <Loader2 className="h-3 w-3 animate-spin" />}
          Add
        </button>
      </form>

      {failure && (
        <p className="mt-2 text-xs text-red-600" role="alert">
          {failure instanceof ApiError && failure.status === 404
            ? "No one with that email has signed in to this instance yet."
            : failure instanceof ApiError && failure.status === 409
              ? "That person already has access to this space."
              : failure.message}
        </p>
      )}
      <p className="mt-2 text-xs" style={{ color: "var(--color-text-muted)" }}>
        {ROLE_BLURB.viewer} Editors {ROLE_BLURB.editor.toLowerCase()}
      </p>
    </div>
  );
}
