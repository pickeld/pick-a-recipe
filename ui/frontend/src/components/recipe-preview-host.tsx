import { useCallback, useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'

import { api } from '@/lib/api'
import { pickUnseenPreview } from '@/lib/preview-queue'
import { useSocketEvent } from '@/lib/socket'
import type { PendingUpload, RecipeData } from '@/types'
import { RecipeEditForm } from '@/components/recipe-edit-form'
import { ImagePicker } from '@/components/image-picker'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'

interface PreviewState {
  uploadId: string
  pendingUpload: PendingUpload
  selectedImageIndex: number
  recipe: RecipeData
}

/**
 * The "show a preview first" dialog. It used to live only on the home page,
 * so a recipe that finished while you were on Tasks or a job never appeared.
 */
export function RecipePreviewHost() {
  const queryClient = useQueryClient()
  const [preview, setPreview] = useState<PreviewState | null>(null)
  const shown = useRef<Set<string>>(new Set())

  const { data } = useQuery({
    queryKey: ['pending-uploads'],
    queryFn: () => api.getPendingUploads(),
    staleTime: 0,
    refetchInterval: 15000,
  })

  const openPreview = useCallback((upload: PendingUpload) => {
    if (!upload.upload_id || !upload.recipe || shown.current.has(upload.upload_id)) return
    shown.current.add(upload.upload_id)
    setPreview({
      uploadId: upload.upload_id,
      pendingUpload: upload,
      selectedImageIndex: upload.best_image_index ?? 0,
      recipe: structuredClone(upload.recipe),
    })
    toast.info(
      `Recipe "${upload.recipe.name || 'Untitled'}" is waiting for your confirmation!`,
    )
  }, [])

  useEffect(() => {
    if (preview) return
    const next = pickUnseenPreview(data?.pending_uploads ?? [], shown.current)
    if (next) openPreview(next)
  }, [data, preview, openPreview])

  useSocketEvent(
    'recipe_preview',
    useCallback(
      (payload: {
        job_id: string
        upload_id: string | null
        recipe: RecipeData
        image_data: string | null
        candidate_images: PendingUpload['candidate_images']
        best_image_index: number
        output_target: string
      }) => {
        if (!payload.upload_id) return
        openPreview({
          upload_id: payload.upload_id,
          job_id: payload.job_id,
          recipe: payload.recipe,
          output_target: payload.output_target,
          best_image_index: payload.best_image_index,
          selected_image_index: payload.best_image_index,
          image_data: payload.image_data ?? undefined,
          candidate_images: payload.candidate_images ?? [],
        })
        void queryClient.invalidateQueries({ queryKey: ['pending-uploads'] })
        void queryClient.invalidateQueries({ queryKey: ['jobs'] })
      },
      [openPreview, queryClient],
    ),
  )

  const confirmUpload = useMutation({
    mutationFn: (state: PreviewState) =>
      api.confirmPendingUpload(state.uploadId, {
        selected_image_index: state.selectedImageIndex,
        recipe: state.recipe,
      }),
    onSuccess: () => {
      setPreview(null)
      void queryClient.invalidateQueries({ queryKey: ['pending-uploads'] })
      void queryClient.invalidateQueries({ queryKey: ['jobs'] })
      toast.success('Upload confirmed!')
    },
    onError: (err: Error) => {
      toast.error(err.message)
    },
  })

  const cancelUpload = useMutation({
    mutationFn: (uploadId: string) => api.cancelPendingUpload(uploadId),
    onSuccess: () => {
      setPreview(null)
      void queryClient.invalidateQueries({ queryKey: ['pending-uploads'] })
      void queryClient.invalidateQueries({ queryKey: ['jobs'] })
      toast.info('Upload cancelled')
    },
    onError: (err: Error) => {
      toast.error(err.message)
    },
  })

  return (
    <Dialog
      open={preview !== null}
      onOpenChange={(open) => {
        if (!open) setPreview(null)
      }}
    >
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Recipe Preview</DialogTitle>
          {preview && (
            <DialogDescription>
              Review before uploading to{' '}
              <span className="font-medium text-foreground">
                {preview.pendingUpload.output_target}
              </span>
            </DialogDescription>
          )}
        </DialogHeader>

        {preview && (
          <div className="space-y-4">
            {(preview.pendingUpload.candidate_images ?? []).length > 0 && (
              <ImagePicker
                images={preview.pendingUpload.candidate_images}
                value={preview.selectedImageIndex}
                onChange={(index) =>
                  setPreview((current) =>
                    current ? { ...current, selectedImageIndex: index } : current,
                  )
                }
              />
            )}
            <RecipeEditForm
              recipe={preview.recipe}
              onChange={(recipe) =>
                setPreview((current) => (current ? { ...current, recipe } : current))
              }
            />
          </div>
        )}

        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => {
              if (preview) cancelUpload.mutate(preview.uploadId)
            }}
            disabled={cancelUpload.isPending}
          >
            Cancel
          </Button>
          <Button
            onClick={() => {
              if (preview) confirmUpload.mutate(preview)
            }}
            disabled={confirmUpload.isPending}
          >
            Confirm Upload
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
