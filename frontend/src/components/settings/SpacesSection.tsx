/**
 * Spaces the caller can reach, and who else can reach the ones they own.
 *
 * Only owners see the member controls — an editor can fill a space with
 * content but cannot widen access to it, and the API enforces that
 * independently.
 */

import { type FormEvent, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ChevronDown,
  ChevronRight,
  Link2,
  Loader2,
  Pencil,
  Plus,
  Users,
} from "lucide-react";
import {
  addMember,
  createSpace,
  fetchDirectory,
  fetchMembers,
  fetchMySpaces,
  removeMember,
  updateMemberRole,
  updateSpace,
  type DirectoryUser,
  type Space,
  type SpaceMember,
  type SpaceRole,
} from "@/api/spaces";
import { ApiError } from "@/api/client";
import type { Me } from "@/api/me";
import SpaceInheritance from "./SpaceInheritance";

const ROLE_BLURB: Record<SpaceRole, string> = {
  viewer: "Can read items, attachments and notes.",
  editor: "Can also add, edit and delete content.",
  owner: "Can also manage who has access.",
};

export default function SpacesSection({ user }: { user: Me }) {
  const spaces = useQuery<Space[], ApiError>({
    queryKey: ["spaces"],
    queryFn: () => fetchMySpaces(),
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
          <SpaceRow key={space.id} space={space} isAdmin={user.is_admin} />
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

/** Which expandable panel a space row is showing, if any. */
type Panel = "members" | "inherits" | "rename";

function SpaceRow({
  space,
  isAdmin,
}: {
  space: Space;
  /** Instance admin. Lets them rename a shared space they don't own —
   *  a label change, which grants no access to what it holds. */
  isAdmin: boolean;
}) {
  const [panel, setPanel] = useState<Panel | null>(null);
  const toggle = (which: Panel) =>
    setPanel((current) => (current === which ? null : which));

  // Nobody renames somebody else's personal shelf, admin or not.
  const canRename = space.is_owner || (isAdmin && !space.is_personal);

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
        {canRename && (
          <PanelButton
            label="Rename"
            Icon={Pencil}
            active={panel === "rename"}
            onClick={() => toggle("rename")}
          />
        )}
        {space.is_owner && (
          <>
            <PanelButton
              label="Sharing"
              Icon={Users}
              active={panel === "members"}
              onClick={() => toggle("members")}
            />
            <PanelButton
              label="Inheritance"
              Icon={Link2}
              active={panel === "inherits"}
              onClick={() => toggle("inherits")}
            />
          </>
        )}
      </div>
      {canRename && panel === "rename" && (
        <RenameForm space={space} onDone={() => setPanel(null)} />
      )}
      {space.is_owner && panel === "members" && <MemberList slug={space.slug} />}
      {space.is_owner && panel === "inherits" && (
        <SpaceInheritance space={space} />
      )}
    </li>
  );
}

/**
 * Change a space's name, and — for a shared space — its slug.
 *
 * A personal space's slug is fixed: `is_personal` is derived from the
 * `u-` prefix, so moving it would quietly reclassify the space. The name
 * is still free to change.
 */
function RenameForm({
  space,
  onDone,
}: {
  space: Space;
  onDone: () => void;
}) {
  const qc = useQueryClient();
  const [name, setName] = useState(space.name);
  const [slug, setSlug] = useState(space.slug);

  const slugChanged = !space.is_personal && slug.trim() !== space.slug;

  const save = useMutation({
    mutationFn: () =>
      updateSpace(space.slug, {
        ...(name.trim() !== space.name ? { name: name.trim() } : {}),
        ...(slugChanged ? { slug: slug.trim() } : {}),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["spaces"] });
      // The slug keys the item listing, the tag list and everything else
      // addressed by it, so a slug change invalidates broadly.
      qc.invalidateQueries({ queryKey: ["items"] });
      qc.invalidateQueries({ queryKey: ["subscribable"] });
      onDone();
    },
  });

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (!name.trim() || save.isPending) return;
    save.mutate();
  }

  return (
    <form
      onSubmit={onSubmit}
      className="mt-2 border-t pt-2"
      style={{ borderColor: "var(--color-border)" }}
    >
      <div className="flex flex-wrap gap-1">
        <input
          required
          value={name}
          onChange={(e) => setName(e.target.value)}
          aria-label={`Name for ${space.name}`}
          placeholder="Space name"
          className="min-w-0 flex-1 rounded border px-2 py-1 text-xs"
          style={{
            borderColor: "var(--color-border)",
            backgroundColor: "var(--color-surface)",
          }}
        />
        <input
          value={slug}
          onChange={(e) => setSlug(e.target.value)}
          disabled={space.is_personal}
          aria-label={`Slug for ${space.name}`}
          placeholder="slug"
          className="min-w-0 flex-1 rounded border px-2 py-1 text-xs disabled:opacity-50"
          style={{
            borderColor: "var(--color-border)",
            backgroundColor: "var(--color-surface)",
          }}
        />
        <button
          type="submit"
          disabled={save.isPending || !name.trim()}
          className="flex shrink-0 items-center gap-1 rounded border px-2 py-1 text-xs hover:opacity-80 disabled:opacity-50"
          style={{ borderColor: "var(--color-border)" }}
        >
          {save.isPending && <Loader2 className="h-3 w-3 animate-spin" />}
          Save
        </button>
        <button
          type="button"
          onClick={onDone}
          className="shrink-0 rounded border px-2 py-1 text-xs hover:opacity-80"
          style={{ borderColor: "var(--color-border)" }}
        >
          Cancel
        </button>
      </div>
      <p className="mt-1 text-xs" style={{ color: "var(--color-text-muted)" }}>
        {space.is_personal
          ? "A personal space's slug is fixed, but you can call it whatever you like."
          : slugChanged
            ? "Changing the slug changes this space's URL. Links people already have will stop working — nothing else breaks."
            : "The slug is what appears in URLs."}
      </p>
      {save.error && (
        <p className="mt-1 text-xs text-red-600" role="alert">
          {save.error instanceof ApiError && save.error.status === 409
            ? "Another space already has that slug."
            : save.error.message}
        </p>
      )}
    </form>
  );
}

function PanelButton({
  label,
  Icon,
  active,
  onClick,
}: {
  label: string;
  Icon: typeof Users;
  active: boolean;
  onClick: () => void;
}) {
  const Chevron = active ? ChevronDown : ChevronRight;
  return (
    <button
      onClick={onClick}
      aria-expanded={active}
      className="flex shrink-0 items-center gap-1 rounded border px-2 py-1 text-xs hover:opacity-80"
      style={{ borderColor: "var(--color-border)" }}
    >
      <Chevron className="h-3.5 w-3.5" />
      <Icon className="h-3.5 w-3.5" />
      {label}
    </button>
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

  const directory = useQuery<DirectoryUser[], ApiError>({
    queryKey: ["directory"],
    queryFn: fetchDirectory,
    retry: (_a, err) => err.status >= 500,
  });

  // Anyone already in the space — the owner included — has nothing to add.
  const existing = new Set(
    (members.data ?? []).map((m) => m.email.toLowerCase()),
  );
  const addable = (directory.data ?? []).filter(
    (u) => !existing.has(u.email.toLowerCase()),
  );

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["members", slug] });
  };

  const add = useMutation({
    mutationFn: () => addMember(slug, email, role),
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
    if (!email || add.isPending) return;
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
        <select
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          aria-label="Person to add"
          disabled={directory.isLoading || addable.length === 0}
          className="min-w-0 flex-1 rounded border px-2 py-1 text-xs disabled:opacity-50"
          style={{
            borderColor: "var(--color-border)",
            backgroundColor: "var(--color-surface)",
          }}
        >
          <option value="">
            {directory.isLoading
              ? "Loading people…"
              : addable.length === 0
                ? "Everyone already has access"
                : "Choose someone…"}
          </option>
          {addable.map((u) => (
            <option key={u.id} value={u.email}>
              {u.display_name} ({u.email})
            </option>
          ))}
        </select>
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
          disabled={add.isPending || !email}
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
