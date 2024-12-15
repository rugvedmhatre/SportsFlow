import os

# start_clip = 0
# end_clip = 8497

start_clip = 6235
end_clip = 7443

for i in range(start_clip, end_clip + 1):
    clip_name = f"/scratch/rrm9598/hpml/acv/SportsSloMo/SportsSloMo_video/clip_{i:04d}.mp4"
    output_folder = f"/scratch/rrm9598/hpml/acv/SportsSloMo/SportsSloMo_frames/clip_{i:04d}/"
    
    os.makedirs(output_folder, exist_ok=True)
    
    cmd = f"ffmpeg -i {clip_name} -start_number 0 {output_folder}frame_%04d.png"
    os.system(cmd)
    print(f"Processed clip_{i:04d}.mp4")

print("All clips have been decoded.")
