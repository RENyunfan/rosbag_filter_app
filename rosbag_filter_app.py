import tkinter as tk
from tkinter import ttk, messagebox
import subprocess
import rosbag
import os
import rospy


class ROSBagFilterApp:
    def __init__(self, root):
        self.root = root
        self.root.title("ROS Bag Filter")

        # Configure the grid to make widgets resize with the window
        root.grid_rowconfigure(5, weight=1)  # Make the topics listbox resize vertically
        root.grid_columnconfigure(1, weight=1)  # Make column 1 resize horizontally

        # Input Bag File
        self.input_bag_label = tk.Label(root, text="Input Bag File:")
        self.input_bag_label.grid(row=0, column=0, padx=10, pady=10, sticky="w")
        self.input_bag_entry = tk.Entry(root, width=50)
        self.input_bag_entry.grid(row=0, column=1, padx=10, pady=10, sticky="ew")
        self.input_bag_button = tk.Button(root, text="Browse", command=self.browse_input_bag)
        self.input_bag_button.grid(row=0, column=2, padx=10, pady=10, sticky="e")

        # Output Bag File
        self.output_bag_label = tk.Label(root, text="Output Bag File:")
        self.output_bag_label.grid(row=1, column=0, padx=10, pady=10, sticky="w")
        self.output_bag_entry = tk.Entry(root, width=50)
        self.output_bag_entry.grid(row=1, column=1, padx=10, pady=10, sticky="ew")
        self.output_bag_button = tk.Button(root, text="Browse", command=self.browse_output_bag)
        self.output_bag_button.grid(row=1, column=2, padx=10, pady=10, sticky="e")

        # Time Mode Radio Buttons
        self.time_mode_var = tk.StringVar(value="ros")
        self.ros_radio = tk.Radiobutton(root, text="ROS Time", variable=self.time_mode_var, value="ros",
                                        command=self.update_time_mode)
        self.ros_radio.grid(row=2, column=0, padx=10, pady=10, sticky="w")
        self.relative_radio = tk.Radiobutton(root, text="Relative Time (seconds)", variable=self.time_mode_var,
                                             value="relative", command=self.update_time_mode)
        self.relative_radio.grid(row=2, column=1, padx=10, pady=10, sticky="w")

        # Start Time
        self.start_time_label = tk.Label(root, text="Start Time:")
        self.start_time_label.grid(row=3, column=0, padx=10, pady=10, sticky="w")
        self.start_time_entry = tk.Entry(root, width=50)
        self.start_time_entry.grid(row=3, column=1, padx=10, pady=10, sticky="ew")

        # End Time
        self.end_time_label = tk.Label(root, text="End Time:")
        self.end_time_label.grid(row=4, column=0, padx=10, pady=10, sticky="w")
        self.end_time_entry = tk.Entry(root, width=50)
        self.end_time_entry.grid(row=4, column=1, padx=10, pady=10, sticky="ew")

        # Topics List
        self.topics_label = tk.Label(root, text="Select Topics to Filter:")
        self.topics_label.grid(row=5, column=0, padx=10, pady=10, sticky="nw")
        self.topics_listbox = tk.Listbox(root, selectmode=tk.MULTIPLE, width=50, height=10)
        self.topics_listbox.grid(row=5, column=1, padx=10, pady=10, sticky="nsew")

        # Progress Bar
        self.progress_bar = ttk.Progressbar(root, orient="horizontal", length=400, mode="determinate")
        self.progress_bar.grid(row=6, column=1, padx=10, pady=10, sticky="ew")

        # Log Pad (Text Widget)
        self.log_pad = tk.Text(root, height=10, width=60, state=tk.DISABLED)
        self.log_pad.grid(row=7, column=1, padx=10, pady=10, sticky="nsew")

        # Filter Button
        self.filter_button = tk.Button(root, text="Filter Bag", command=self.filter_bag)
        self.filter_button.grid(row=8, column=1, padx=10, pady=10, sticky="e")

        self.total_messages = 0  # To store the total number of messages
        self.bag = None
        self.out_bag = None
        self.start_time_bag = None  # To store the start time of the bag

    def browse_input_bag(self):
        input_bag = self.gnome_file_browser("Select Input Bag File")
        if input_bag:
            # Automatically add .bag extension if not present
            if not input_bag.endswith(".bag"):
                self.input_bag_entry.delete(0, tk.END)
                messagebox.showerror("Error", "Input bag file must have .bag extension.")
                return

            self.input_bag_entry.delete(0, tk.END)
            self.input_bag_entry.insert(0, input_bag)

            # Close previously opened bag if any
            if self.bag:
                self.bag.close()

            self.bag = rosbag.Bag(input_bag)
            self.total_messages = self.bag.get_message_count()

            # Get start and end times of the bag
            self.start_time = self.bag.get_start_time()
            self.end_time = self.bag.get_end_time()
            self.start_time_bag = self.start_time

            # Set default start and end times in ROS time
            self.start_time_entry.delete(0, tk.END)
            self.start_time_entry.insert(0, str(self.start_time))

            self.end_time_entry.delete(0, tk.END)
            self.end_time_entry.insert(0, str(self.end_time))

            # Print the number of messages loaded
            self.log_message(f"Loaded {self.total_messages} messages from the input bag.")
            self.log_message(f"Bag start time (ROS Time): {self.start_time}")
            self.log_message(f"Bag end time (ROS Time): {self.end_time}")

            # Automatically load topics after setting the input bag
            self.load_topics()

            # set the default output file to the same directory and namely filtered_${original_name}.bag
            output_bag = os.path.join(os.path.dirname(input_bag), f"filtered_{os.path.basename(input_bag)}")


            self.output_bag_entry.delete(0, tk.END)
            self.output_bag_entry.insert(0, output_bag)
            # Close previously opened output bag if any
            if self.out_bag:
                self.out_bag.close()

            self.out_bag = rosbag.Bag(output_bag, 'w')

    def browse_output_bag(self):
        output_bag = self.gnome_file_browser("Save Output Bag File", save=True)
        if output_bag:
            if (not output_bag.endswith(".bag")):
                output_bag += ".bag"

            self.output_bag_entry.delete(0, tk.END)
            self.output_bag_entry.insert(0, output_bag)

            # Close previously opened output bag if any
            if self.out_bag:
                self.out_bag.close()

            self.out_bag = rosbag.Bag(output_bag, 'w')

    def gnome_file_browser(self, title="Select File", save=False):
        try:
            if save:
                result = subprocess.run(['zenity', '--file-selection', '--save', '--title', title],
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            else:
                result = subprocess.run(['zenity', '--file-selection', '--title', title],
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE)

            if result.returncode == 0:  # File selected
                return result.stdout.decode('utf-8').strip()
            else:  # No file selected or canceled
                return None
        except Exception as e:
            self.log_message(f"Error using file browser: {str(e)}")
            return None

    def load_topics(self):
        input_bag = self.input_bag_entry.get()
        if not os.path.exists(input_bag):
            messagebox.showerror("Error", "Input bag file does not exist.")
            return

        self.topics_listbox.delete(0, tk.END)

        if self.bag:
            topics = self.bag.get_type_and_topic_info()[1].keys()
            for topic in topics:
                self.topics_listbox.insert(tk.END, topic)
            self.log_message(f"Loaded {len(topics)} topics from the input bag.")
        else:
            messagebox.showerror("Error", "No input bag loaded.")

    def log_message(self, message):
        # Enable the text widget, insert the log message, then disable it again
        self.log_pad.config(state=tk.NORMAL)
        self.log_pad.insert(tk.END, message + "\n")
        self.log_pad.see(tk.END)  # Auto-scroll to the end
        self.log_pad.config(state=tk.DISABLED)
        self.root.update_idletasks()  # Ensure the UI updates

    def update_time_mode(self):
        """ Update the label and content based on the selected time mode. """
        time_mode = self.time_mode_var.get()
        if time_mode == "ros":
            self.start_time_label.config(text="Start Time (ROS Time):")
            self.end_time_label.config(text="End Time (ROS Time):")
            # Set default start and end times in ROS time
            self.start_time_entry.delete(0, tk.END)
            self.start_time_entry.insert(0, str(self.start_time))

            self.end_time_entry.delete(0, tk.END)
            self.end_time_entry.insert(0, str(self.end_time))
        else:
            self.start_time_label.config(text="Start Time (Relative):")
            self.end_time_label.config(text="End Time (Relative):")
            # Set default start and end times in ROS time
            self.start_time_entry.delete(0, tk.END)
            self.start_time_entry.insert(0, '0')

            self.end_time_entry.delete(0, tk.END)
            self.end_time_entry.insert(0, str(self.end_time - self.start_time))

    def filter_bag(self):
        if not self.bag or not self.out_bag:
            self.log_message("Error: No input or output bag file selected.")
            messagebox.showerror("Error", "No input or output bag file selected.")
            return

        selected_topics = [self.topics_listbox.get(i) for i in self.topics_listbox.curselection()]

        if not selected_topics:
            self.log_message("Error: No topics selected.")
            messagebox.showerror("Error", "No topics selected.")
            return

        # Convert start and end times based on the selected mode
        time_mode = self.time_mode_var.get()
        try:
            if time_mode == "ros":
                start_time = rospy.Time.from_sec(float(self.start_time_entry.get()))
                end_time = rospy.Time.from_sec(float(self.end_time_entry.get()))
            else:
                # Relative time mode
                start_time_relative = float(self.start_time_entry.get())
                end_time_relative = float(self.end_time_entry.get())
                start_time = rospy.Time.from_sec(self.start_time_bag + start_time_relative)
                end_time = rospy.Time.from_sec(self.start_time_bag + end_time_relative)
        except ValueError:
            self.log_message("Error: Invalid time format.")
            messagebox.showerror("Error", "Invalid time format.")
            return

        self.log_message("Starting the filtering process...")

        # Initialize progress bar
        self.progress_bar["maximum"] = (self.total_messages * (end_time - start_time).to_sec() / (
                    self.bag.get_end_time() - self.bag.get_start_time()))
        self.progress_bar["value"] = 0

        for i, (topic, msg, t) in enumerate(self.bag.read_messages(start_time=start_time, end_time=end_time)):
            if topic in selected_topics:
                self.out_bag.write(topic, msg, t)
            # Log progress every 1000 messages
            if i % 1000 == 0:
                self.log_message(f"Processed {i} messages...")
            # Update progress bar
            self.progress_bar["value"] = i + 1
            self.root.update_idletasks()

        self.log_message("Filtering process completed.")
        messagebox.showinfo("Success", f"Filtered bag saved as {self.output_bag_entry.get()}")

        # Close the bags after filtering is complete
        self.bag.close()
        self.out_bag.close()
        self.bag = None
        self.out_bag = None
        self.total_messages = 0
        self.input_bag_entry.delete(0, tk.END)
        self.output_bag_entry.delete(0, tk.END)
        self.topics_listbox.delete(0, tk.END)
        self.time_mode_var.set("ros")
        self.start_time_label.config(text="Start Time (ROS Time):")
        self.end_time_label.config(text="End Time (ROS Time):")
        # Set default start and end times in ROS time
        self.start_time_entry.delete(0, tk.END)
        self.end_time_entry.delete(0, tk.END)


if __name__ == "__main__":
    root = tk.Tk()
    app = ROSBagFilterApp(root)
    root.mainloop()

