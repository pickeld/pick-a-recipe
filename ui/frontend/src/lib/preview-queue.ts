export interface PreviewCandidate {
  upload_id?: string | null
}

/**
 * The next approval the preview dialog should open.
 *
 * The dialog is mounted for every page, so this only cares which uploads the
 * user has not already been shown. A recipe that finishes on Tasks or a job
 * page is the same queue as one that finishes on the home page.
 */
export function pickUnseenPreview<T extends PreviewCandidate>(
  uploads: readonly T[],
  shown: ReadonlySet<string>,
): T | undefined {
  return uploads.find((upload) => {
    const id = upload.upload_id
    return typeof id === 'string' && id.length > 0 && !shown.has(id)
  })
}
